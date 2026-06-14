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

真实供应商 Live wiring（连接/双向泵/事件解析）在 M1 接上（Gemini Live · google.genai 2.8）；
MOCK_LIVE=1 走脱云桩，让中继 + 客户端 UI 可脱云联调（契约·MOCK）：不连真会话，但 text.input
注入时回一条 canned assistant transcript（让开放对话动线离线能显字幕），并可模拟一次
look_at_page function_call 走通抓帧往返。桩不连网、不抛未捕获异常。
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import time
from typing import Awaitable, Callable, Optional

from contracts import (
    Channel,
    Envelope,
    Interrupted,
    MessageType,
    Mode,
    SessionReady,
    SessionStart,
    SessionUpdate,
    Transcript,
    TranscriptRole,
    VoiceMode,
)
from contracts.mock import is_mock
from contracts.tools import MODE_TOOLSETS, TOOL_REGISTRY, ToolName
from contracts.vision import VisionKind
from server.llm import providers
from server.relay import tool_dispatch
from server.relay.tool_dispatch import VisionBudget
from server.skills import prompts

log = logging.getLogger("va.live")

_KEEPALIVE_PATCHED = False


def _patch_live_keepalive(cfg: dict) -> None:
    """方案A：放宽 Gemini Live 的 ws keepalive ping（PRD §5 China→Google 实时流抖动容忍）。

    google-genai 用 websockets 默认 keepalive（ping_interval=20s / ping_timeout=20s）开 Live ws，
    且 connect 时不传 ping 参数（live.py 的 `ws_connect(uri, additional_headers=..., **ssl)`）。
    实测：China→Google 链路在真实双向音频重载下 pong 20s 内回不来 → 误判连接死、以 1011 掐断
    （轻载能撑 40s）。故此处进程级一次性 monkeypatch SDK 的 `ws_connect`，注入 config 调的 ping 参数：
      · session.live_ping_timeout_ms   等 pong 容忍上限（默认放宽 20s→60s）。
      · session.live_ping_interval_ms  ping 间隔；置 0 → None = 禁 keepalive ping（靠数据流/TCP 探活）。
    只放宽、不（默认）禁用：仍留有限 keepalive，免真死连接变僵尸。阈值进 config（禁硬编码，契约·配置）。
    """
    global _KEEPALIVE_PATCHED
    if _KEEPALIVE_PATCHED:
        return
    session = cfg.get("session", {}) or {}
    interval_ms = int(session.get("live_ping_interval_ms", 20000))
    timeout_ms = int(session.get("live_ping_timeout_ms", 60000))
    ping_interval = interval_ms / 1000 if interval_ms > 0 else None
    ping_timeout = timeout_ms / 1000 if timeout_ms > 0 else None
    import google.genai.live as _genai_live  # 延迟导入：MOCK_LIVE 路径零外部依赖

    _genai_live.ws_connect = functools.partial(
        _genai_live.ws_connect, ping_interval=ping_interval, ping_timeout=ping_timeout
    )
    _KEEPALIVE_PATCHED = True
    log.info("Live keepalive 放宽：ping_interval=%ss ping_timeout=%ss", ping_interval, ping_timeout)


def _is_transient_connect_error(e: BaseException) -> bool:
    """Live 连接 __aenter__ 的异常是否「瞬时」（值得重试）。

    瞬时（弱链路一抖即过，重试多半成功）：连接重置/超时/TLS·握手抖动。ConnectionResetError 是 OSError
    子类，故 OSError 一网打尽连接层错误（含 SOCKS/TLS 握手 reset）。永久错误（鉴权失败/模型不存在的
    APIError、NotImplementedError、配置/解析错）不在此返回 True → 不重试、直接抛，免空耗（PRD §5）。
    """
    if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError, OSError)):
        return True
    name = type(e).__name__  # websockets 握手/连接关闭等按类型名近似，不强依赖 websockets 导入
    return any(k in name for k in ("ConnectionClosed", "Handshake", "InvalidState", "WebSocketException"))


SendEvent = Callable[[Envelope], Awaitable[None]]
SendAudio = Callable[[bytes], Awaitable[None]]

# observe 唯一带参工具：可选 hint（看什么）。其余工具无入参（识题/批改即刻抓当前帧；
# weather_get 的 lat/lon 由系统注入/回落，不让模型现编坐标）。工具入参 schema = 契约真源的最小映射。
_TOOL_PARAM_HINT = "可选：聚焦看什么（如 outfit / 手里的物体）。"


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
        # 客户端定位（navigator.geolocation 经 session.start 注入）：weather_get 用，缺省 → 执行体回落默认城市。
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None

        budget = int(cfg.get("session", {}).get("vision_budget_per_problem", 3))
        self.vision_budget = VisionBudget(budget)

        # 摄像头单帧往返：request_id → Future（frame.response 到达时 set_result）。
        self._pending_frames: dict[str, asyncio.Future] = {}
        self._frame_seq = 0
        self._model_task: Optional[asyncio.Task] = None
        self._live = None  # 供应商 Live 会话句柄（真实路径：AsyncSession；MOCK 下恒 None）
        self._live_cm = None  # connect() 返回的异步上下文管理器（aclose 时 __aexit__ 收尾）
        self._closing = False  # 正在主动关/切会话：泵任务的 receive() 异常视为预期，不报 live_disconnected（防误降级）
        self._mock_demo_done = False  # MOCK：look_at_page function_call 演示只跑一次，避免抓帧风暴
        self._live_error_emitted = False  # 方案B：本会话生命周期内 live_disconnected 只报一次（防刷屏）

        # 单帧往返超时（PRD §5 抓帧确定性绿层）：客户端漏回 frame.response 时不让模型泵死锁。
        self.frame_timeout_ms = int(cfg.get("session", {}).get("frame_timeout_ms", 4000))

    # ── 生命周期 ───────────────────────────────────────────────────────────────
    async def start(self, start: SessionStart) -> None:
        """开 Live 会话：按 mode 注入系统提示 profile + 工具子集，回 session.ready。"""
        self.mode = start.mode
        self.voice_mode = start.voice_mode
        self.subtitles = start.subtitles
        self._lat = start.lat  # 生活模式天气定位（缺省/拒绝 → weather_get 回落默认城市）
        self._lon = start.lon
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
        """会话结束：先停泵（取消并 await）再关 Live 会话（无落盘——会话记忆在 Live 侧，随会话销毁）。

        次序铁律（F1/F2）：必须先 await 泵任务真正退出，**再**让 __aexit__ 关 ws——否则泵仍卡在
        self._live.receive() 那条 ws 上，__aexit__ 先关 ws 会让 receive() 炸进 except 误报 live_disconnected。
        """
        self._closing = True  # 标记主动关闭：泵收尾期的 receive() 异常视为预期，不误报降级（F2）
        await self._stop_pump()
        await self._close_live_session()

    async def _stop_pump(self) -> None:
        """取消并 await 泵任务退出（F1）：cancel 后必须 await 让它真正停在 receive() 之外再继续。

        泵内对 CancelledError 是 `raise` 透传，故此处吞掉 CancelledError 即为「干净退出」。
        """
        task, self._model_task = self._model_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
        """运行时切换（mode/voice_mode/subtitles）。

        mode 与 voice_mode 都进会话建连 config（mode→系统提示 profile + 工具子集；voice_mode→
        automatic_activity_detection.disabled 的 VAD 策略），故二者任一变化都需**重开会话**才能生效。
        subtitles 只影响下行字幕过滤（本地状态），无需重开。
        早先只在 mode 变化时重配 → 切 voice_mode（PTT↔自由）服务端 VAD 策略不变：从 PTT 切自由后服务端
        仍停在手动 VAD、等 activity 边界，而自由对话不发边界 → 自由对话「说话无反应」（M2-04 回归）。
        """
        if upd.subtitles is not None:
            self.subtitles = upd.subtitles
        reconfigure = False
        if upd.voice_mode is not None and upd.voice_mode != self.voice_mode:
            self.voice_mode = upd.voice_mode  # VAD 策略在会话 config 里，须重开会话才换得动
            reconfigure = True
        if upd.mode is not None and upd.mode != self.mode:
            self.mode = upd.mode  # 换系统提示 profile + 工具子集
            reconfigure = True
        if reconfigure:
            await self._reconfigure_session()

    # ── 工具往返（拉）──────────────────────────────────────────────────────────
    def _reset_budget_on_new_problem(self, names: list[str]) -> None:
        """新一题边界 → 视觉预算归零（F5）。在一批 function_calls **处理之前**整体判一次，

        而不是在 per-call 循环里每遇 look_at_page 就 reset：同一条 LiveServerMessage 可能同时带
        look_at_page + check_draft，循环内 reset 受调用顺序摆布（先批改后识题会把批改的计数清掉）。
        以「这批里出现了 look_at_page」作为新题信号、批前 reset 一次，预算计数对调用顺序稳健。
        """
        if any(n == ToolName.LOOK_AT_PAGE.value for n in names):
            self.vision_budget.reset()  # 新一题 → 视觉预算归零（识题1 + 批改≤2，PRD §5）

    async def _on_function_call(self, name: str, args: dict) -> dict:
        """Live 模型 function_call → tool_dispatch（含视觉预算）→ 结果 dict（回灌 function_response）。

        伴随发 tool.activity（start/done）给客户端做 UI 反馈；learning 模式下 look_at_page 完成即
        让客户端置 active_problem（坐姿放行门控读它，PRD §3.2.2）。预算 reset 移到批级
        _reset_budget_on_new_problem（F5），本函数不再单点 reset。

        帧不可用兜底（F3）：抓帧往返超时（客户端漏回 frame.response）→ 不抛进泵、不死锁，回一个
        「帧不可用」function_response，让模型改请用户口述（与「超预算」走同一类降级语义，PRD §5）。
        """
        await self._emit_tool_activity(name, "start")
        # 生活模式天气定位：weather_get 的 lat/lon 由系统注入（客户端 geolocation 经 session.start 传入），
        # 不让模型现编坐标（契约九 / 铁律）；未定位则不注入 → 执行体回落默认城市（PRD §5）。
        if name == ToolName.WEATHER_GET.value and self._lat is not None and self._lon is not None:
            args = {**args, "lat": self._lat, "lon": self._lon}
        try:
            result = await tool_dispatch.dispatch(
                name, args, acquire_frame=self._acquire_frame, budget=self.vision_budget, cfg=self.cfg
            )
        except (asyncio.TimeoutError, TimeoutError):
            log.warning("抓帧往返超时（%dms）：%s 回帧不可用兜底", self.frame_timeout_ms, name)
            result = {"frame_unavailable": True, "note": "没拿到画面，请用户念给我听"}
        except Exception as e:
            # 工具执行体内部出错（如某工具尚未实现 / 外部 API 失败）：**绝不**让它冒泡进泵把整条 Live
            # 会话掀翻（否则一次工具失败 = 全会话 live_disconnected）。回一个 error function_response，
            # 让模型据此对用户诚实说明「这个暂时帮不上」（PRD §5 工具失败回落、绝不阻塞；确定性绿层）。
            # CancelledError 是 BaseException，不被此处吞，仍正常向上取消。
            log.warning("工具执行出错（已隔离，不拖垮会话）：%s → %s", name, e)
            result = {"error": True, "note": f"{name} 暂时不可用，请对用户诚实说明帮不上这件事"}
        await self._emit_tool_activity(name, "done")
        return result

    async def _acquire_frame(self, kind: VisionKind) -> bytes:
        """发 frame.request → 等客户端 frame.response（配对 request_id）→ 返回 JPEG 字节。

        超时硬上限（F3，PRD §5 抓帧确定性绿层）：客户端漏回 frame.response 时，不让模型泵无限期
        卡在这里（那会冻住整条 function_call→response 回灌，连后续音频/打断都接不上）。超时即抛
        TimeoutError，由 _on_function_call 兜成「帧不可用」function_response，让模型改请用户口述。
        request_id 必须无论成败都从 _pending_frames 清掉，避免迟到的 frame.response 找不到 future 而泄漏。
        """
        self._frame_seq += 1
        request_id = f"f-{self._frame_seq:04d}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_frames[request_id] = fut
        await self._send_event(
            self._envelope(
                MessageType.FRAME_REQUEST,
                Channel.FRAME,
                {"request_id": request_id, "kind": kind.value},
            )
        )
        try:
            # frame_timeout_ms 从 config 读（禁硬编码超时魔数，契约·配置）。
            jpeg_b64 = await asyncio.wait_for(fut, timeout=self.frame_timeout_ms / 1000)
        finally:
            self._pending_frames.pop(request_id, None)  # 成功/超时/取消都清，杜绝悬挂 future 泄漏
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

    # ── 供应商 Live wiring（真实路径 = Gemini Live；MOCK_LIVE 走脱云桩）────────────
    def _build_live_config(self):
        """据当前 mode/voice_mode 组装 LiveConnectConfig（系统提示 profile + 工具子集 + 音色 + VAD）。

        所有契约值/模型名/音色/采样率从 config 读，不硬编码（CLAUDE.md §4 / 契约·配置）：
          · system_instruction = skills.system_for_mode(mode)（提示词权属 E）。
          · tools = MODE_TOOLSETS[mode] 子集，逐名从 TOOL_REGISTRY 生成 FunctionDeclaration（契约·工具单一真源）。
          · 音色 = config.roles.live.voice；响应仅 AUDIO（Live 大脑自带 TTS）。
          · 上/下行转写均开，供前端字幕（契约·transcript）。
          · PTT（voice_mode==ptt）禁自动 VAD、用 activity 信号界定轮次；free 开自动 VAD（PRD §4.3/§4.4）。
        """
        from google.genai import types

        live_role = self.cfg["roles"]["live"]
        voice_name = live_role["voice"]  # 音色进 config，禁硬编码（契约·配置）

        ptt = self.voice_mode == VoiceMode.PTT
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=prompts.system_for_mode(self.mode),
            tools=self._declare_tools(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=ptt)
            ),
        )

    def _declare_tools(self) -> list:
        """把 mode 工具子集声明给 Live 会话（契约·工具 = 单一真源；不手抄白名单）。

        每个工具名 → TOOL_REGISTRY[name] 取 intent 作 description；仅 observe 带可选 hint 参数，
        其余无参（识题/批改即刻抓帧；weather_get 的 lat/lon 由系统注入/回落，不让模型现编坐标）。
        """
        from google.genai import types

        decls = []
        for name in MODE_TOOLSETS[self.mode.value]:
            spec = TOOL_REGISTRY[name]
            params = None
            if name == ToolName.OBSERVE.value:
                params = types.Schema(
                    type=types.Type.OBJECT,
                    properties={"hint": types.Schema(type=types.Type.STRING, description=_TOOL_PARAM_HINT)},
                )
            decls.append(
                types.FunctionDeclaration(name=name, description=spec.intent, parameters=params)
            )
        return [types.Tool(function_declarations=decls)]

    async def _open_live_session(self) -> None:
        """连接供应商 Live 会话：注入 mode 的系统提示 profile + 工具子集声明 + 音色 + VAD 策略。

        按 provider 分派（F4）：当前 **Live 仅支持 gemini**——
          · gemini → providers.client_for_role("live") → client.aio.live.connect(...)（异步上下文管理器，
            本桥长持有，aclose 时 __aexit__ 收尾）。系统提示 profile + 工具子集在 _build_live_config 注入。
          · openai → google.genai 那套 .aio.live API 不存在（gpt-realtime 走另一套 SDK），直接清晰抛
            NotImplementedError，**不**让 client.aio.live 撞出难懂的 AttributeError。
        config.roles.live.provider 决定走哪支（密注：Live 当前是 gemini-only，切 openai 需先补 realtime wiring）。
        MOCK_LIVE：不连真会话（self._live 恒 None），_pump_model_output 直接退出；
        text.input/posture 注入由脱云桩在 _inject_* 内回 canned transcript。
        """
        self._live_error_emitted = False  # 新会话：重置降级单发标记（方案B）
        if is_mock("MOCK_LIVE"):
            self._live = None  # 无真实会话；脱云桩在 _inject_* / 抓帧往返里造可见效果
            return

        provider = self.cfg["roles"]["live"]["provider"]  # provider 进 config（契约·配置）
        if provider != "gemini":
            # Live 当前仅接 Gemini Live；openai realtime（gpt-realtime）尚未 wiring，给出清晰报错而非
            # 在 client.aio.live 上撞 AttributeError（F4：config 允许切 openai，但本路径未实现）。
            raise NotImplementedError(
                f"Live 会话当前仅支持 provider=gemini，config.roles.live.provider={provider!r} 暂未接入"
            )
        _patch_live_keepalive(self.cfg)  # 方案A：放宽 ws keepalive（进程级只打一次，扛 China→Google 抖动）
        client = providers.client_for_role("live", self.cfg)
        model = self.cfg["roles"]["live"]["model"]  # 模型名进 config（契约·配置）

        # 连接重试（方案C，PRD §5 China→Google 实时流抖动）：__aenter__ 的 TLS/SOCKS 握手在弱链路上
        # 偶发 ConnectionResetError 等**瞬时**错误，单次连接一抖就整会话失败、前端弹「连接断开」。这里对
        # 瞬时连接错误做有限重试（次数/退避进 config，禁硬编码），多数一抖即过；非瞬时错误（鉴权/模型/
        # NotImplementedError）不重试、直接抛由上层降级。仅 __aenter__ 成功后才持有 _live_cm（失败的 cm
        # 不留存，免 aclose 时对未进入的 cm 调 __aexit__）。
        session_cfg = self.cfg.get("session", {}) or {}
        retry_max = int(session_cfg.get("live_connect_retry_max", 2))
        backoff_ms = int(session_cfg.get("live_connect_retry_backoff_ms", 400))
        attempt = 0
        while True:
            cm = client.aio.live.connect(model=model, config=self._build_live_config())
            try:
                self._live = await cm.__aenter__()
                self._live_cm = cm  # 仅成功时持有，供 aclose 时 __aexit__ 收尾
                if attempt:
                    log.info("Live 连接在第 %d 次重试后成功", attempt)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                attempt += 1
                if attempt > retry_max or not _is_transient_connect_error(e):
                    raise  # 重试耗尽 / 非瞬时错误 → 抛出，由上层降级（_emit_live_error / server_error）
                log.warning(
                    "Live 连接第 %d 次失败（%s: %s），%dms 后重试",
                    attempt, type(e).__name__, e, backoff_ms,
                )
                await asyncio.sleep(backoff_ms / 1000)

    async def _reconfigure_session(self) -> None:
        """切 mode 后换系统提示 profile + 工具子集（Gemini Live 经重开会话注入新 config）。

        重开前后泵任务连续：先停旧泵（取消并 await 退出，F1）+ 关旧会话，重开后重启泵
        （_apply_update 在 start 之后调，故此处自管泵任务的停/重建，保证新会话的输出有人接）。
        关旧会话期间置 _closing，让旧泵收尾的 receive() 异常视为预期、不误报 live_disconnected（F2）。
        """
        self._closing = True  # 切 mode 是主动关旧会话，旧泵收尾异常视为预期（F2，防误发降级 UI）
        try:
            await self._stop_pump()  # 先 await 旧泵真正退出，再关旧 ws（F1：杜绝 receive() 撞已关 ws）
            await self._close_live_session()
            await self._open_live_session()
        finally:
            self._closing = False  # 新会话已开，恢复正常断流检测
        self._model_task = asyncio.create_task(self._pump_model_output())

    async def _close_live_session(self) -> None:
        """关供应商 Live 会话（__aexit__ 收尾连接）。MOCK 下无真实会话，空操作。"""
        cm, self._live_cm, self._live = self._live_cm, None, None
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception as e:  # 关连接异常不应拖垮收尾
                log.warning("关 Live 会话异常（忽略）：%s", e)

    async def _kill_live(self, detail: str) -> None:
        """方案B：标记 Live 会话已死——置 None（后续 send 经 `is None` 短路）+ 只报一次 error。

        发送侧与接收泵都汇到这里：第一处发现断流即降级，绝不让 ConnectionClosedError 冒泡拖垮
        客户端 WS（= 前端「连接断开」），也不刷屏 N 次 server_error（PRD §5/§8 优雅降级）。
        """
        self._live = None
        await self._emit_live_error(detail)

    async def _send_to_live(self, label: str, make_coro) -> None:
        """对 self._live 的发送统一兜底（方案B）：连接已断即 _kill_live、不冒泡拖垮客户端 WS。

        make_coro = 现造协程的无参 lambda（如 lambda: self._live.send_realtime_input(...)）；
        确认 self._live 非 None 后才调用，避免对 None 取属性。CancelledError 透传（正常取消）。
        """
        if self._live is None:
            return
        try:
            await make_coro()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # ConnectionClosed 等 → 降级，不拖垮 WS
            await self._kill_live(f"{label}: {e}")

    async def _send_audio_to_model(self, pcm: bytes) -> None:
        """PCM16 → Live 会话音频上行。采样率从 config.session.audio_in_sample_rate 读（禁硬编码）。"""
        if is_mock("MOCK_LIVE") or self._live is None:
            return
        from google.genai import types

        in_rate = self.cfg["session"]["audio_in_sample_rate"]
        await self._send_to_live(
            "audio",
            lambda: self._live.send_realtime_input(
                audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={in_rate}")
            ),
        )

    async def _model_activity(self, start: bool) -> None:
        """PTT 边界 → Live 会话 activity_start/activity_end（对讲机轮次，PRD §4.3）。

        仅 PTT 模式有意义；free 模式由模型原生 VAD 判轮次，客户端本就不发这两条边界。
        """
        if is_mock("MOCK_LIVE") or self._live is None:
            return
        from google.genai import types

        kw = (
            {"activity_start": types.ActivityStart()}
            if start
            else {"activity_end": types.ActivityEnd()}
        )
        await self._send_to_live("activity", lambda: self._live.send_realtime_input(**kw))

    async def _inject_text(self, text: str) -> None:
        """文字输入兜底 → 注入 Live 会话当作一个完整用户轮次（PRD §5/§8 语音降级）。"""
        if not text:
            return
        if is_mock("MOCK_LIVE"):
            await self._mock_canned_reply(text)  # 脱云桩：回一条 canned assistant 字幕
            return
        if self._live is None:
            return
        from google.genai import types

        await self._send_to_live(
            "text",
            lambda: self._live.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]),
                turn_complete=True,
            ),
        )

    async def _inject_posture(self, payload: dict) -> None:
        """posture.alert → 作为简短 text 事件推进 Live 会话（推，非 function call；PRD §3.2/§4.2）。

        客户端已过放行门控（mode/active_problem）+ gap 闸门并自带 reminder_count；本层只把
        「驼背了，第 N 次」这一事实作为 text 注入，让模型用 proactive 决定措辞与最终择时（措辞权属 E）。
        """
        count = payload.get("reminder_count")  # 客户端透传的本会话累计次数（计数真源在客户端）
        nth = f"第 {count} 次" if count else "又一次"
        fact = f"[坐姿提醒事实] 用户驼背了，{nth}。请你自行决定怎么说、什么时候说出来。"
        if is_mock("MOCK_LIVE") or self._live is None:
            return  # MOCK：不连会话；坐姿动线属 M3，桩不造假字幕以免误导
        from google.genai import types

        # turn_complete=True（择时决策，M3-04）：客户端 gap 闸门已保证只在下行静默 ≥gap_min_silence_ms
        # 时才发 alert（此刻模型没在说话，不存在「打断关键思路」），故 True 不会切断在播音频；且坐姿核心
        # 场景是孩子**静默书写**时驼背——没有用户轮次可搭载，必须由本事实主动触发模型发声，True 才能可靠
        # 念出提醒（turn_complete=False 需 Gemini proactivity，而在未核验的 preview 模型上开 proactivity
        # 有回归 Live 连接的风险）。「最多等一句、不抢话」由 skills 提示词软上界兜（M3-05）。
        # 待办（M3-06 真机）：核验模型 proactivity 能力后，再评估是否切 proactive + turn_complete=False。
        await self._send_to_live(
            "posture",
            lambda: self._live.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=fact)]),
                turn_complete=True,
            ),
        )

    async def _pump_model_output(self) -> None:
        """泵 Live 会话输出 → 客户端：音频下行 / transcript / interrupted / function_call 派发。

        逐字段判空（Live 事件多字段可能为 None）：
          · msg.data（bytes）= PCM24 下行音频 → send_audio。
          · server_content.input_transcription → 用户字幕；output_transcription → 助手字幕（仅 subtitles 时下发）。
          · server_content.interrupted → 发 interrupted 事件（客户端停播+清队列，PRD §4.4）。
          · tool_call.function_calls → 逐个 _on_function_call → send_tool_response 回灌 function_response。
        transcript.final 取 SDK 的 transcription.finished（None 时近似为 False=流式增量，契约·transcript）。

        多轮（关键）：google-genai 的 `session.receive()` 是**单轮**异步生成器——遇第一个
        server_content.turn_complete 即 break 退出（一次 `async for` 只覆盖一个模型轮次）。故必须外套
        `while True:` 每轮重新 receive()，否则第 1 轮结束后泵任务就退出、后续轮次的下行音频/字幕/打断/
        function_call 全部无人读（= PTT 只响应一轮、自由对话切过去后全程无声的根因）。会话关闭/切换
        （_live 置 None 或 _closing）时跳出循环干净收尾；真断流由 receive() 抛异常 → 下方 except 处置。
        """
        if is_mock("MOCK_LIVE") or self._live is None:
            return  # 脱云桩：无真实会话；可见效果由 _inject_text / 抓帧往返造（见 _inject_text / on_client_event）

        from google.genai import types

        try:
            while True:
                # 每轮重新 receive()：上一轮 turn_complete 让生成器退出后，回到这里取下一轮（多轮关键）。
                if self._live is None or self._closing:
                    break  # 会话已关/正在切换：停泵，交 aclose/_reconfigure 收尾（不误报降级）
                async for msg in self._live.receive():
                    # —— 下行音频（PCM24）——
                    data = msg.data
                    if data:
                        await self._send_audio(data)

                    sc = msg.server_content
                    if sc is not None:
                        if sc.input_transcription is not None:
                            await self._emit_transcript(TranscriptRole.USER, sc.input_transcription)
                        if sc.output_transcription is not None:
                            await self._emit_transcript(
                                TranscriptRole.ASSISTANT, sc.output_transcription
                            )
                        if sc.interrupted:
                            await self._send_event(
                                self._envelope(
                                    MessageType.INTERRUPTED,
                                    Channel.AUDIO,
                                    Interrupted(reason="barge_in").model_dump(),
                                )
                            )

                    # —— function_call 派发（拉）——
                    tc = msg.tool_call
                    if tc is not None and tc.function_calls:
                        # 批级预算 reset（F5）：同一条消息可能带 look_at_page + check_draft，先整体判
                        # 一次新题边界再逐个派发，预算计数不受 function_calls 顺序摆布。
                        self._reset_budget_on_new_problem([fc.name for fc in tc.function_calls])
                        responses = []
                        for fc in tc.function_calls:
                            result = await self._on_function_call(fc.name, dict(fc.args or {}))
                            responses.append(
                                types.FunctionResponse(id=fc.id, name=fc.name, response=result)
                            )
                        await self._live.send_tool_response(function_responses=responses)
        except asyncio.CancelledError:
            raise  # 会话切换/关闭时正常取消，向上传播
        except Exception as e:  # Live 断流/解析异常：报 error 事件 + 退出泵（不拖垮 WS）
            if self._closing or self._live is None:
                # 主动关/切会话期间 receive() 撞已关 ws 抛的异常属预期收尾，不报 live_disconnected（F2：
                # 否则正常结束/切 mode 每次都误发降级 UI，错误触发文字输入兜底）。
                log.debug("Live 输出泵收尾期异常（主动关闭，忽略）：%s", e)
                return
            log.warning("Live 输出泵异常：%s", e)
            # 会话已死：汇到 _kill_live（置 None 短路后续 send + 只报一次降级；方案B）。
            await self._kill_live(str(e))

    async def _emit_transcript(self, role: TranscriptRole, transcription) -> None:
        """把 Live 转写片段转成 transcript 事件（仅 subtitles 开时下发；空文本跳过）。"""
        if not self.subtitles:
            return
        text = getattr(transcription, "text", None)
        if not text:
            return
        final = bool(getattr(transcription, "finished", False))  # None → False=流式增量
        await self._send_event(
            self._envelope(
                MessageType.TRANSCRIPT,
                Channel.TRANSCRIPT,
                Transcript(role=role, text=text, final=final).model_dump(),
            )
        )

    async def _emit_live_error(self, detail: str) -> None:
        """Live 断流 → error 事件（客户端据 degradation 切字幕/文字输入兜底；PRD §5/§8）。

        幂等（方案B）：每个会话生命周期只发一次——发送侧 N 帧音频 + 接收泵都汇到这里，
        避免刷屏 N 次 live_disconnected/server_error。新会话在 _open_live_session 重置标记。
        """
        if self._live_error_emitted:
            return
        self._live_error_emitted = True
        log.warning("Live 链路降级（live_disconnected）：%s", detail)
        from contracts import Degradation, ErrorEvent

        await self._send_event(
            self._envelope(
                MessageType.ERROR,
                Channel.CONTROL,
                ErrorEvent(
                    code="live_disconnected",
                    message="语音链路中断，可改用文字输入。",
                    degradation=Degradation.FALLBACK_TEXT,
                ).model_dump(),
            )
        )

    # ── MOCK_LIVE 脱云桩（不连网、不抛未捕获异常）──────────────────────────────────
    async def _mock_canned_reply(self, user_text: str) -> None:
        """脱云桩：收到 text.input 即回一条 canned assistant transcript，让开放对话动线离线能显字幕。

        额外（演示一次抓帧往返）：首次文字注入若提及看/题/画面，模拟一次 look_at_page function_call
        走通 frame.request/response 往返 + 视觉预算（MOCK_VISION 读 fixture），打通拉链路。
        桩全程不连网、不抛未捕获异常（契约·MOCK）。
        """
        if self.subtitles:
            await self._send_event(
                self._envelope(
                    MessageType.TRANSCRIPT,
                    Channel.TRANSCRIPT,
                    Transcript(role=TranscriptRole.USER, text=user_text, final=True).model_dump(),
                )
            )

        # 演示抓帧往返：需客户端回 frame.response 才会完成（否则一直等），故默认不主动抓帧——
        # 保持桩「不挂起」。置 LIVE_MOCK_DEMO_FRAME=1 可演示一次 look_at_page 拉链路（非依赖 MOCK，
        # 是纯调试开关，故不登记 contracts/mock.py；走 os.getenv 直读，不混入 is_mock 体系）。
        if (
            not self._mock_demo_done
            and os.getenv("LIVE_MOCK_DEMO_FRAME") == "1"
            and ToolName.LOOK_AT_PAGE.value in MODE_TOOLSETS[self.mode.value]
        ):
            self._mock_demo_done = True
            try:
                # 与真实泵一致：批级 reset 在派发前（F5）；桩里演示也走同一新题边界口径。
                self._reset_budget_on_new_problem([ToolName.LOOK_AT_PAGE.value])
                await self._on_function_call(ToolName.LOOK_AT_PAGE.value, {})
            except Exception as e:  # 抓帧往返失败不应让桩抛出（契约·MOCK：零外部依赖、不挂连接）
                log.warning("MOCK look_at_page 演示往返失败（忽略）：%s", e)

        if self.subtitles:
            reply = "（离线桩）收到，我在听。开放对话动线已接通，可以继续说。"
            await self._send_event(
                self._envelope(
                    MessageType.TRANSCRIPT,
                    Channel.TRANSCRIPT,
                    Transcript(
                        role=TranscriptRole.ASSISTANT, text=reply, final=True
                    ).model_dump(),
                )
            )
