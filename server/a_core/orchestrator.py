"""A · 反应式 agent 编排循环（契约八；PRD §7.2 伪码见 §1.4）。M1-02 骨架级空跑。

每个 asr.final 触发一次循环（本批只「空跑贯通」，不接真 planner / 真工具 / 真护栏）：
  1. 感知 asr.final + 工作记忆 → 组 planner 输入
  2. planner（deepseek-chat，温度0+结构化）→ PlannerOutput{kind, mode, tools, text}
     MOCK_PLANNER=1 走固定脚本（不调 LLM，契约六）；本批只实现 MOCK 路径，真 LLM 留 M2。
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


async def call_planner(planner_input: dict) -> PlannerOutput:
    """调 planner（结构化输出 PlannerOutput）；MOCK_PLANNER=1 返回固定脚本。

    本批只实现 MOCK 路径（脱依赖、可独立空跑，契约六）；真 LLM 调用（deepseek-chat，
    温度0 + 结构化 + 软超时 planner_timeout_ms）留 M2，超时即维持现场景（PRD §7.2）。
    """
    if is_mock("MOCK_PLANNER"):
        return _mock_planner(planner_input)
    raise NotImplementedError("M2：真 planner（deepseek-chat 温度0+结构化+软超时）")


def _guardrail_decide(text: str, memory, cfg: dict) -> str:
    """**护栏调用位（铁律3/5）**：所有出站文本经此单一闸门，M2 在此接 guardrails 裁决。

    本批为占位直通（不实现护栏体、不调 guardrails.* 的 NotImplementedError）：原样返回 text。
    M2 将在此调用 guardrails（answer_guard / 置信门控产物等），planner 不得绕过本闸门
    直产 tts.say（铁律3），且本闸门在编排循环之外、planner 不可覆盖（铁律5）。
    """
    return text


async def run_turn(asr_final: AsrFinal, memory, cfg: Optional[dict] = None) -> list[TtsSay]:
    """处理一个用户回合：asr.final → planner → (dispatch≤max_tool_rounds) → 护栏 → tts.say。

    返回经护栏闸门收口的 tts.say 列表（A 只发 tts.say 给 B，铁律2）。本批为骨架空跑：
    工具结果不深加工（MOCK 桩 ack），仅验证链路贯通与 max_tool_rounds 截顶。

    cfg：可注入（测试用）；缺省 load_config()。max_tool_rounds 读 config（禁硬编码，契约七）。
    """
    if cfg is None:
        cfg = load_config()
    max_tool_rounds = int(cfg.get("orchestration", {}).get("max_tool_rounds", 0))

    planner_input = _build_planner_input(asr_final, memory)
    plan = await call_planner(planner_input)

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
            await dispatch.dispatch(tool_call)
            rounds += 1
        candidate = FALLBACK_TEXT if capped else (plan.text or FALLBACK_TEXT)
    elif plan.kind == PlannerKind.CLARIFY:
        candidate = plan.text or FALLBACK_TEXT
    else:  # ANSWER 快路径
        candidate = plan.text or FALLBACK_TEXT

    # 护栏闸门（铁律3/5）→ tts.say（铁律2：A 只发 tts.say 给 B）。
    decided = _guardrail_decide(candidate, memory, cfg)
    return [TtsSay(text=decided, turn_id=asr_final.turn_id, seq=0)]
