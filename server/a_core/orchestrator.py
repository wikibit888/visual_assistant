"""A · 反应式 agent 编排循环（契约八；PRD §7.2 伪码见 §1.4）。

每个 asr.final 触发一次循环：
  1. 感知 asr.final + 工作记忆 → 组 planner 输入
  2. planner（deepseek-chat，温度0+结构化）→ PlannerOutput{kind, mode, tools, text}
     MOCK_PLANNER=1 走固定脚本（不调 LLM，契约六）；否则 M2-01 接真 planner：
     deepseek-chat（OpenAI 兼容）温度0 + JSON 结构化 + 软超时 planner_timeout_ms，
     超时/异常即维持现场景（PRD §7.2：兜底 answer，mode 沿用上一回合，落 FALLBACK_TEXT）。
  3. kind==answer  → 直接候选回复（快路径）
     kind==tool_calls → 经 dispatch 顺序执行工具，**受 config.orchestration.max_tool_rounds
       截顶**（触顶停、走 FALLBACK_TEXT），组合 → 候选回复
     kind==clarify  → 澄清文本（真正的 clarify_max 约束在 M2 护栏）
  4. 候选回复 → 确定性护栏裁决（guardrails，循环外不可被 planner 覆盖，铁律3/5）→ tts.say

**护栏调用位（铁律3）**：本批仅 import guardrails 占位、不实现护栏体——所有出站候选文本
经 _guardrail_decide() 这一「单一闸门」收口为 tts.say，M2 在此处接 guardrails 裁决。
planner 不得绕过此闸门直产 TTS（铁律3）；闸门在编排循环之外、planner 不可覆盖（铁律5）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from contracts.orchestration import (
    Mode,
    PlannerKind,
    PlannerOutput,
    ToolCall,
    ToolName,
)
from contracts.voice import AsrFinal, TtsSay
from contracts.config_schema import load_config
from contracts.mock import is_mock
from server.a_core import dispatch
from server.e_skills import prompts
from server.llm.providers import client_for_role

# 护栏调用位（铁律3/5）：仅 import 占位，本批不实现护栏体（guardrails.* 由其它任务落地）。
from server.a_core import guardrails  # noqa: F401  护栏裁决闸门，M2 在 _guardrail_decide 接入

# 触顶 / planner 失败时的兜底话术（措辞最终归 E；本批占位常量，避免阻塞首响，PRD §7.2）。
FALLBACK_TEXT = "我先这样回应，稍后再细看。"


def _build_planner_input(asr_final: AsrFinal, memory) -> dict:
    """感知 asr.final + 工作记忆 → planner 输入（本批为结构占位，真 prompt 组装在 M2/E）。"""
    return {
        "text": asr_final.text,
        "turn_id": asr_final.turn_id,
        "current_mode": getattr(getattr(memory, "current_mode", None), "value", None),
    }


def _mock_planner(planner_input: dict) -> PlannerOutput:
    """MOCK_PLANNER 固定脚本（不调 LLM，契约六）：按用户文本派生确定性 PlannerOutput。

    脚本极简、可彩排（与真 planner 同形：结构化 PlannerOutput）：
    - 文本含「多工具」→ tool_calls（observe + read_problem + check_draft），mode=life
      （故意排 3 个 vision 工具，>默认 max_tool_rounds=2，用于验证「触顶停」；
       全部 vision 工具，仅依赖 MOCK_VISION，不外溢本批 MOCK 面 MOCK_PLANNER+MOCK_VISION）
    - 文本含「题」→ tool_calls（read_problem），mode=learning（演示工具往返）
    - 文本含「？」「?」→ clarify
    - 否则 → answer（快路径）
    自由度只在 text 措辞（铁律：kind/工具选择不自由），脚本不含路由分类器（铁律4）。
    """
    text = (planner_input.get("text") or "").strip()
    if "多工具" in text:
        return PlannerOutput(
            kind=PlannerKind.TOOL_CALLS,
            mode=Mode.LIFE,
            tools=[
                ToolCall(name=ToolName.OBSERVE),
                ToolCall(name=ToolName.READ_PROBLEM),
                ToolCall(name=ToolName.CHECK_DRAFT),
            ],
            text=None,
        )
    if "题" in text:
        return PlannerOutput(
            kind=PlannerKind.TOOL_CALLS,
            mode=Mode.LEARNING,
            tools=[ToolCall(name=ToolName.READ_PROBLEM)],
            text=None,
        )
    if "？" in text or "?" in text:
        return PlannerOutput(
            kind=PlannerKind.CLARIFY,
            mode=Mode.OPEN,
            tools=[],
            text="你是想问哪一部分呢？",
        )
    return PlannerOutput(
        kind=PlannerKind.ANSWER,
        mode=Mode.OPEN,
        tools=[],
        text=f"收到：{text}",
    )


def _sticky_mode(planner_input: dict) -> Mode:
    """维持现场景：从 planner_input.current_mode 还原上一回合 mode，缺失/非法回落 OPEN。

    call_planner 不接 memory（保签名稳定、不破坏既有不传 cfg 的测试），故 sticky 信息
    经 _build_planner_input 注入的 current_mode（Mode.value 字符串或 None）携带。
    """
    raw = planner_input.get("current_mode")
    try:
        return Mode(raw) if raw is not None else Mode.OPEN
    except ValueError:
        return Mode.OPEN


def _maintain_scene(planner_input: dict) -> PlannerOutput:
    """planner 软超时/异常时的「维持现场景」兜底（PRD §7.2）。

    kind=answer（不发起工具）、mode 沿用上一回合（sticky）、tools=[]、text=None
    → run_turn 落到 FALLBACK_TEXT，由确定性护栏闸门收口为 tts.say（铁律3/5）。
    """
    return PlannerOutput(
        kind=PlannerKind.ANSWER,
        mode=_sticky_mode(planner_input),
        tools=[],
        text=None,
    )


async def _real_planner_call(planner_input: dict, cfg: dict) -> PlannerOutput:
    """真 planner 网络调用（deepseek-chat，OpenAI 兼容）：温度0 + JSON 结构化 → PlannerOutput。

    抽成模块级独立协程，便于测试 monkeypatch 替换（离线断言超时/解析/快路径，零真实网络）。
    - 客户端经 client_for_role("planner", cfg) 取（模型名/base/key 全走 config+.env，零硬编码）。
    - system = E 的 prompts.PLANNER_SYSTEM（措辞归 E，A 只引用）；user = 本回合识别文本。
    - 温度取 cfg.roles.planner.temperature；structured_output 为真则请求 JSON object（契约七）。
    - 同步 OpenAI SDK 阻塞调用必须用 asyncio.to_thread 包起，不在事件循环里阻塞。
    """
    planner_cfg = (cfg.get("roles", {}) or {}).get("planner", {}) or {}
    temperature = planner_cfg.get("temperature")
    structured = bool(planner_cfg.get("structured_output", False))

    client = client_for_role("planner", cfg)
    user_text = (planner_input.get("text") or "").strip()

    create_kwargs: dict = {
        "model": client.model if hasattr(client, "model") else planner_cfg.get("model"),
        "messages": [
            {"role": "system", "content": prompts.PLANNER_SYSTEM},
            {"role": "user", "content": user_text},
        ],
    }
    if temperature is not None:
        create_kwargs["temperature"] = temperature
    if structured:
        create_kwargs["response_format"] = {"type": "json_object"}

    resp = await asyncio.to_thread(client.chat.completions.create, **create_kwargs)
    content = resp.choices[0].message.content
    return PlannerOutput.model_validate(json.loads(content))


async def call_planner(planner_input: dict, cfg: Optional[dict] = None) -> PlannerOutput:
    """调 planner（结构化输出 PlannerOutput）；MOCK_PLANNER=1 返回固定脚本。

    - MOCK_PLANNER=1 → 走 _mock_planner 固定脚本（零网络、零外部依赖，契约六），短路返回，
      不读 cfg、不进超时逻辑（保既有不传 cfg 的测试可用）。
    - 否则 → 真 planner：cfg 缺省 load_config()，软超时 = cfg.roles.planner.planner_timeout_ms
      （禁硬编码，契约七）包住 _real_planner_call；超时(asyncio.TimeoutError)或任何调用/解析
      异常 → 维持现场景兜底（PRD §7.2），绝不抛给 run_turn（稳定性第一）。
    """
    if is_mock("MOCK_PLANNER"):
        return _mock_planner(planner_input)

    cfg = cfg or load_config()
    planner_cfg = (cfg.get("roles", {}) or {}).get("planner", {}) or {}
    timeout_s = int(planner_cfg.get("planner_timeout_ms", 0)) / 1000.0

    try:
        if timeout_s > 0:
            # 软超时（asyncio.wait_for 触发 TimeoutError → 下方兜底维持现场景）。
            return await asyncio.wait_for(
                _real_planner_call(planner_input, cfg), timeout=timeout_s
            )
        return await _real_planner_call(planner_input, cfg)
    except asyncio.TimeoutError:
        # 软超时 → 维持现场景（PRD §7.2），不阻塞首响。
        return _maintain_scene(planner_input)
    except Exception:
        # 网络/解析/任何调用异常同样兜底维持现场景（稳定性第一，绝不抛给 run_turn）。
        return _maintain_scene(planner_input)


def _guardrail_decide(text: str, memory, cfg: dict) -> str:
    """**护栏调用位（铁律3/5）**：所有出站文本经此单一闸门，M2 在此接 guardrails 裁决。

    本批为占位直通（不实现护栏体、不调 guardrails.* 的 NotImplementedError）：原样返回 text。
    M2 将在此调用 guardrails（answer_guard / 置信门控产物等），planner 不得绕过本闸门
    直产 tts.say（铁律3），且本闸门在编排循环之外、planner 不可覆盖（铁律5）。
    """
    return text


async def run_turn(asr_final: AsrFinal, store, cfg: Optional[dict] = None) -> list[TtsSay]:
    """处理一个用户回合：asr.final → planner → (dispatch≤max_tool_rounds) → 护栏 → tts.say。

    返回经护栏闸门收口的 tts.say 列表（A 只发 tts.say 给 B，铁律2）。本批为活体接线：
    工具经会话级 store 分发（memory_* 可用），仍验证链路贯通与 max_tool_rounds 截顶。

    store：会话级 WorkingMemoryStore（A 权属，PRD §7.1）——持结构化 store.memory（供 planner
      读 prior mode、写回 current_mode）+ 通用 KV（memory_* 工具）。经 dispatch(..., store=...)
      透传给 get_tool，让 memory_note/recall 绑定本会话。
    cfg：可注入（测试用）；缺省 load_config()。max_tool_rounds 读 config（禁硬编码，契约七）。
    """
    if cfg is None:
        cfg = load_config()
    max_tool_rounds = int(cfg.get("orchestration", {}).get("max_tool_rounds", 0))

    # 读 prior mode（sticky）须在 mode 写回之前：_build_planner_input 取 store.memory.current_mode。
    planner_input = _build_planner_input(asr_final, store.memory)
    # 传 cfg：真 planner 分支据 cfg 读温度/软超时（禁硬编码，契约七）；MOCK 分支短路不用 cfg。
    plan = await call_planner(planner_input, cfg)

    # mode 写回（契约八 sticky）：planner 裁定本回合 mode → 写入工作记忆，供下一回合 sticky。
    # 在读取 prior mode（上方 _build_planner_input）之后写，不污染本回合 planner 输入。
    store.memory.current_mode = plan.mode

    capped = False
    if plan.kind == PlannerKind.TOOL_CALLS:
        # rails 可注入 hook：agentic 透传 planner 工具，rails 注入 forced_tool_sequence（§7.2）。
        sequence = dispatch.resolve_sequence(plan.tools, plan.mode, cfg)
        rounds = 0
        for tool_call in sequence:
            if rounds >= max_tool_rounds:
                # 触顶停（PRD §7.2）：剩余工具不再执行，候选回复走 FALLBACK_TEXT。
                capped = True
                break
            # 透传 store：memory_* 工具绑定会话级 store（get_tool 按 name 注入），无状态工具忽略。
            await dispatch.dispatch(tool_call, store=store)
            rounds += 1
        candidate = FALLBACK_TEXT if capped else (plan.text or FALLBACK_TEXT)
    elif plan.kind == PlannerKind.CLARIFY:
        candidate = plan.text or FALLBACK_TEXT
    else:  # ANSWER 快路径
        candidate = plan.text or FALLBACK_TEXT

    # 护栏闸门（铁律3/5）→ tts.say（铁律2：A 只发 tts.say 给 B）。
    decided = _guardrail_decide(candidate, store.memory, cfg)
    return [TtsSay(text=decided, turn_id=asr_final.turn_id, seq=0)]
