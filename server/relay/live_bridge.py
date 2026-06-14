"""中继 · Live 会话桥（新 M0 骨架）。每个 WS 连接一座 LiveBridge。

塌进 Live 模型后，这是后端的脊梁：把**客户端单 WS** 与**供应商 Live 会话**对接起来。
两条方向相反的数据流（PRD §4.1）：

  拉（模型→工具）：Live 模型发 function_call → tool_dispatch 派发工具执行体 → function_response 回灌。
  推（端侧→模型）：客户端 posture.alert → 作为 text 事件注入 Live 会话（非 function call）。

LiveBridge 不做任何编排决策（那是 Live 模型的事）；它只做确定性的「翻译 + 闸门 + 计数」：
  · 客户端控制事件 ⇄ Live 会话动作（session 配置 / PTT 边界 activityStart-End / 文字输入注入）。
  · Live 输出 → 客户端消息（音频下行 / transcript / tool.activity / interrupted / frame.request）。
  · function_call → tool_dispatch（视觉预算在此层，PRD §5）→ function_response。

依赖注入两条出客户端的通道（由 server/main.py 的 WS 端点提供）：
  send_event(Envelope) —— 发控制/事件 JSON 帧；  send_audio(bytes) —— 发 PCM24 音频二进制帧。

真实供应商 Live wiring（连接/双向泵/事件解析）标 NotImplementedError，留并行开发（M1）接；
MOCK_LIVE=1 走脚本/回声桩，让中继 + 客户端 UI 可脱云联调（契约·MOCK）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from contracts import (
    Channel,
    Envelope,
    MessageType,
    Mode,
    SessionReady,
    SessionStart,
    SessionUpdate,
    VoiceMode,
)
from contracts.mock import is_mock
from contracts.tools import MODE_TOOLSETS
from contracts.vision import VisionKind
from server.relay import tool_dispatch
from server.relay.tool_dispatch import VisionBudget
from server.skills import prompts

SendEvent = Callable[[Envelope], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]


class LiveBridge:
    """单 WS 连接的 Live 会话桥。生命周期：start → (on_client_audio / on_client_event)* → aclose。"""

    def __init__(self, cfg: dict, send_event: SendEvent, send_audio: SendAudio) -> None:
        self.cfg = cfg
        self._send_event = send_event
        self._send_audio = send_audio

        self.session_id: Optional[str] = None
        self.mode: Mode = Mode.OPEN
        self.voice_mode: VoiceMode = VoiceMode.PTT
        self.subtitles: bool = True

        budget = int(cfg.get("session", {}).get("vision_budget_per_problem", 3))
        self.vision_budget = VisionBudget(budget)

        # 摄像头单帧往返：request_id → Future（frame.response 到达时 set_result）。
        self._pending_frames: dict[str, asyncio.Future] = {}
        self._frame_seq = 0
        self._model_task: Optional[asyncio.Task] = None
        self._live = None  # 供应商 Live 会话句柄（真实路径）

    # ── 生命周期 ───────────────────────────────────────────────────────────────
    async def start(self, start: SessionStart) -> None:
        """开 Live 会话：按 mode 注入系统提示 profile + 工具子集，回 session.ready。"""
        self.mode = start.mode
        self.voice_mode = start.voice_mode
        self.subtitles = start.subtitles
        self.session_id = f"s-{int(time.time() * 1000):x}"

        await self._open_live_session()
        # 泵 Live 输出 → 客户端（后台任务，贯穿会话）。
        self._model_task = asyncio.create_task(self._pump_model_output())
        await self._send_event(
            self._envelope(
                MessageType.SESSION_READY,
                Channel.SESSION,
                SessionReady(
                    session_id=self.session_id, mode=self.mode, voice_mode=self.voice_mode
                ).model_dump(),
            )
        )

    async def aclose(self) -> None:
        """会话结束：关 Live 会话 + 取消泵任务（无落盘——会话记忆在 Live 侧，随会话销毁）。"""
        if self._model_task:
            self._model_task.cancel()
        await self._close_live_session()

    # ── 客户端 → 后端 ──────────────────────────────────────────────────────────
    async def on_client_audio(self, pcm: bytes) -> None:
        """客户端 PCM16 上行 → 转发进 Live 会话（半双工 gate 在客户端，本层只透传）。"""
        await self._send_audio_to_model(pcm)

    async def on_client_event(self, env: Envelope) -> None:
        """客户端控制帧路由。仅处理客户端→后端方向的 type；其余忽略。"""
        t = env.type
        if t == MessageType.SESSION_UPDATE:
            await self._apply_update(SessionUpdate.model_validate(env.payload))
        elif t == MessageType.INPUT_ACTIVITY_START:
            await self._model_activity(start=True)
        elif t == MessageType.INPUT_ACTIVITY_END:
            await self._model_activity(start=False)
        elif t == MessageType.FRAME_RESPONSE:
            self._resolve_frame(env.payload["request_id"], env.payload["jpeg_base64"])
        elif t == MessageType.POSTURE_ALERT:
            await self._inject_posture(env.payload)
        elif t == MessageType.TEXT_INPUT:
            await self._inject_text(env.payload.get("text", ""))

    async def _apply_update(self, upd: SessionUpdate) -> None:
        """运行时切换（mode/voice_mode/subtitles）。切 mode = 换 profile + 工具子集（可能需重开会话）。"""
        if upd.subtitles is not None:
            self.subtitles = upd.subtitles
        if upd.voice_mode is not None:
            self.voice_mode = upd.voice_mode
        if upd.mode is not None and upd.mode != self.mode:
            self.mode = upd.mode
            await self._reconfigure_session()  # 换系统提示 profile + 工具子集

    # ── 工具往返（拉）──────────────────────────────────────────────────────────
    async def _on_function_call(self, name: str, args: dict) -> dict:
        """Live 模型 function_call → tool_dispatch（含视觉预算）→ 结果 dict（回灌 function_response）。

        伴随发 tool.activity（start/done）给客户端做 UI 反馈；learning 模式下 look_at_page 完成即
        让客户端置 active_problem（坐姿放行门控读它，PRD §3.2.2）。新识题（look_at_page）reset 预算。
        """
        from contracts.tools import ToolName

        if name == ToolName.LOOK_AT_PAGE.value:
            self.vision_budget.reset()  # 新一题 → 视觉预算归零
        await self._emit_tool_activity(name, "start")
        result = await tool_dispatch.dispatch(
            name, args, acquire_frame=self._acquire_frame, budget=self.vision_budget, cfg=self.cfg
        )
        await self._emit_tool_activity(name, "done")
        return result

    async def _acquire_frame(self, kind: VisionKind) -> bytes:
        """发 frame.request → 等客户端 frame.response（配对 request_id）→ 返回 JPEG 字节。"""
        self._frame_seq += 1
        request_id = f"f-{self._frame_seq:04d}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_frames[request_id] = fut
        await self._send_event(
            self._envelope(
                MessageType.FRAME_REQUEST,
                Channel.FRAME,
                {"request_id": request_id, "kind": kind.value},
            )
        )
        jpeg_b64 = await fut  # frame.response 到达 → _resolve_frame set_result
        import base64

        return base64.b64decode(jpeg_b64)

    def _resolve_frame(self, request_id: str, jpeg_base64: str) -> None:
        fut = self._pending_frames.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(jpeg_base64)

    # ── 出客户端的便捷封装 ──────────────────────────────────────────────────────
    async def _emit_tool_activity(self, name: str, phase: str) -> None:
        await self._send_event(
            self._envelope(
                MessageType.TOOL_ACTIVITY, Channel.TRANSCRIPT, {"name": name, "phase": phase}
            )
        )

    def _envelope(self, mtype: MessageType, channel: Channel, payload: dict) -> Envelope:
        return Envelope(type=mtype, ts=int(time.time() * 1000), channel=channel, payload=payload)

    # ── 供应商 Live wiring（真实路径留 M1；MOCK_LIVE 走桩）────────────────────────
    async def _open_live_session(self) -> None:
        """连接供应商 Live 会话：注入 mode 的系统提示 profile + 工具子集声明。

        profile 取 server.skills.prompts.system_for_mode(self.mode)；工具子集取
        contracts.MODE_TOOLSETS[mode]。真实路径用 providers.client_for_role("live", cfg) 建会话。
        """
        _ = (prompts.system_for_mode(self.mode), MODE_TOOLSETS[self.mode.value])  # 骨架占位引用
        if is_mock("MOCK_LIVE"):
            self._live = None  # MOCK：无真实会话；_pump_model_output 走脚本/回声桩
            return
        raise NotImplementedError(
            "M1：连接供应商 Live 会话（gemini client.aio.live / openai realtime）。"
            "用 providers.client_for_role('live', cfg) 建会话，注入 system_for_mode + 工具子集声明。"
        )

    async def _reconfigure_session(self) -> None:
        """切 mode 后换系统提示 profile + 工具子集（多数 realtime 需重开会话）。M1 实现。"""
        await self._close_live_session()
        await self._open_live_session()

    async def _close_live_session(self) -> None:
        """关供应商 Live 会话。MOCK 下无真实会话，空操作。"""
        self._live = None

    async def _send_audio_to_model(self, pcm: bytes) -> None:
        """PCM16 → Live 会话音频上行。M1 实现（MOCK 下丢弃）。"""
        if is_mock("MOCK_LIVE"):
            return
        raise NotImplementedError("M1：把 PCM16 帧送进供应商 Live 会话音频上行通道")

    async def _model_activity(self, start: bool) -> None:
        """PTT 边界 → Live 会话 activityStart/activityEnd（对讲机轮次，PRD §4.3）。M1 实现。"""
        if is_mock("MOCK_LIVE"):
            return
        raise NotImplementedError("M1：向 Live 会话发 activityStart/End（PTT 轮次边界）")

    async def _inject_text(self, text: str) -> None:
        """文字输入兜底 → 注入 Live 会话当作一个用户轮次（PRD §8）。M1 实现。"""
        if is_mock("MOCK_LIVE"):
            return
        raise NotImplementedError("M1：把 text.input 注入 Live 会话作用户轮次")

    async def _inject_posture(self, payload: dict) -> None:
        """posture.alert → 作为 text 事件推进 Live 会话（推，非 function call；PRD §4.2）。M1 实现。

        客户端已过放行门控（mode/active_problem）+ gap 闸门；本层只把「驼背了，第 N 次」这一事实
        作为 text 注入，模型用 proactive 决定措辞与最终择时。
        """
        if is_mock("MOCK_LIVE"):
            return
        raise NotImplementedError("M1：把 posture.alert 作为 text 事件注入 Live 会话（proactive 择时）")

    async def _pump_model_output(self) -> None:
        """泵 Live 会话输出 → 客户端：音频下行 / transcript / interrupted / function_call 派发。

        真实路径：async for event in live_session → 按事件类型转译成 send_audio / send_event，
        function_call 事件交 _on_function_call 并回灌 function_response。M1 实现。
        MOCK_LIVE：可在此实现一个脚本/回声桩（如收到文本即回固定 transcript），供脱云联调。
        """
        if is_mock("MOCK_LIVE"):
            return  # MOCK 桩：并行开发可在此填脚本回声；新 M0 先空跑不报错
        raise NotImplementedError(
            "M1：解析供应商 Live 事件流 → send_audio(PCM24) / transcript / interrupted；"
            "function_call → _on_function_call → 回灌 function_response"
        )
