"""A · 确定性护栏（agent 不可覆盖；契约三/八/九；PRD §7.4）。M1 骨架。

全部为确定性纯函数，**在编排循环之外**，planner 不可覆盖、提示词不可关闭：
  - confidence_gate   视觉 confidence < config 阈 → 不播报错误/不开讲，改请口述
  - clarify_gate      每 focus 澄清 ≤ config clarify_max
  - loop_gate         每回合工具往返 ≤ config max_tool_rounds，触顶 FALLBACK_TEXT
  - vision_budget_gate 单题视觉 ≤ config vision_budget_per_problem（识题1+批改≤2）
  - sticky_fallback   planner 超时/失败 → 维持 focus（不漂移）
  - answer_guard      出站前正则组合拦截（可选，默认关；契约九）→ 留 M4
护栏盲区（PRD §7.4）：只守「看清/时序/失控」，守不住「LLM 讲错」（语义层），知情接受。

设计脊梁（铁律 3/5/6）：
  * 护栏是**独立确定性函数**，不持有 planner 实例、不接收任何「能关闭它的开关」。
    每条护栏的判定阈值只从 `cfg`（contracts.config_schema.load_config()）读取，
    代码中**不硬编码任何阈值/模型名**（契约七）。
  * confidence_gate / clarify_gate / loop_gate / vision_budget_gate / sticky_fallback
    均无副作用、可在编排循环之外独立调用与单测——「planner 提议，确定性护栏裁决」。
  * answer_guard 是契约九里**唯一**带 config 开关的护栏（demo 默认关），其余护栏无开关。
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from contracts.errors import Degradation
from contracts.vision import Verdict

# 触顶 / 超时 / 兜底统一走的降级动作（语义见契约五）。话术由 E 提供，这里只产降级信号。
FALLBACK_TEXT = Degradation.FALLBACK_TEXT


@dataclass(frozen=True)
class GuardVerdict:
    """护栏对单次裁决的确定性产出（A 内部结构，非跨模块契约）。

    allowed=True  → 放行 planner 的提议（措辞仍由 planner 决定）。
    allowed=False → 护栏否决：编排循环必须改走 degradation（话术 E 提供），
                    planner 无法覆盖此裁决（铁律 5）。
    """

    allowed: bool
    degradation: Optional[Degradation] = None
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def _orch(cfg: dict) -> dict:
    """取 orchestration 段（缺失即配置坏掉，显式报错而非静默用魔数）。"""
    if not isinstance(cfg, dict) or "orchestration" not in cfg:
        raise KeyError("config 缺 orchestration 段（契约七）")
    return cfg["orchestration"]


def confidence_gate(confidence: float, cfg: dict) -> GuardVerdict:
    """置信门控（契约三 / PRD §7.4）。

    视觉 confidence < config orchestration.confidence_gate → 否决：
    不播报错误 / 不开讲，改请用户口述（degradation=ABORT，维持现场景不阻塞首响）。
    阈值只从 config 读，护栏无开关、planner 不可覆盖（铁律 3/5）。

    入参用裸 confidence（float），让 read_problem / check_draft / observe 三类结果共用一条门。
    """
    threshold = float(_orch(cfg)["confidence_gate"])
    if confidence < threshold:
        return GuardVerdict(
            allowed=False,
            degradation=Degradation.ABORT,
            reason="low_confidence",
            detail={"confidence": confidence, "threshold": threshold},
        )
    return GuardVerdict(allowed=True, reason="confidence_ok",
                        detail={"confidence": confidence, "threshold": threshold})


def verdict_gate(check_result, cfg: dict) -> GuardVerdict:
    """批改结果（CheckDraftResult）专用门控：四值 verdict + confidence 联合裁。

    - confidence 低于阈 → 否决（改请用户念该行）。
    - verdict==LOW_CONFIDENCE → 否决（门控拦截，改请念该行）。
    - verdict==UNREADABLE → 否决（诚实兜底，不编造）。
    其余（found_error / all_correct 且置信足）放行。仍是确定性、无开关。
    """
    conf = confidence_gate(check_result.confidence, cfg)
    if not conf.allowed:
        return conf
    if check_result.verdict in (Verdict.LOW_CONFIDENCE, Verdict.UNREADABLE):
        return GuardVerdict(
            allowed=False,
            degradation=Degradation.ABORT,
            reason=f"verdict_{check_result.verdict.value}",
            detail={"verdict": check_result.verdict.value},
        )
    return GuardVerdict(allowed=True, reason=f"verdict_{check_result.verdict.value}",
                        detail={"verdict": check_result.verdict.value})


def clarify_gate(clarify_count: int, cfg: dict) -> GuardVerdict:
    """澄清上限（契约八 / PRD §7.4）：每 focus 澄清 ≤ config orchestration.clarify_max。

    clarify_count = 工作记忆里**当前 focus 已发生**的澄清次数。
    已达上限 → 否决再澄清，改走 FALLBACK_TEXT（别无限追问，演示不卡死）。
    阈值只从 config 读；护栏无开关。
    """
    clarify_max = int(_orch(cfg)["clarify_max"])
    if clarify_count >= clarify_max:
        return GuardVerdict(
            allowed=False,
            degradation=FALLBACK_TEXT,
            reason="clarify_exhausted",
            detail={"clarify_count": clarify_count, "clarify_max": clarify_max},
        )
    return GuardVerdict(allowed=True, reason="clarify_ok",
                        detail={"clarify_count": clarify_count, "clarify_max": clarify_max})


def loop_gate(tool_rounds_done: int, cfg: dict) -> GuardVerdict:
    """loop 上限（契约八 / PRD §7.2/§7.4）：每回合工具往返 ≤ config max_tool_rounds。

    tool_rounds_done = 本回合**已完成**的工具往返轮数。
    触顶（>= max_tool_rounds）→ 否决再发工具，收口走 FALLBACK_TEXT（防 agent 失控空转）。
    rails 模式下 config 把 max_tool_rounds_when_railed=0，但**那是 rails 注入的事**，
    本门只忠实读 orchestration.max_tool_rounds——编排层决定传哪个阈，门只裁。
    """
    max_rounds = int(_orch(cfg)["max_tool_rounds"])
    if tool_rounds_done >= max_rounds:
        return GuardVerdict(
            allowed=False,
            degradation=FALLBACK_TEXT,
            reason="loop_exhausted",
            detail={"tool_rounds_done": tool_rounds_done, "max_tool_rounds": max_rounds},
        )
    return GuardVerdict(allowed=True, reason="loop_ok",
                        detail={"tool_rounds_done": tool_rounds_done, "max_tool_rounds": max_rounds})


def vision_budget_gate(vision_calls_done: int, cfg: dict) -> GuardVerdict:
    """视觉预算（契约八/C8 / PRD §7.4）：单题视觉 ≤ config vision_budget_per_problem。

    vision_calls_done = 当前 active_problem 已花的视觉调用数（识题1 + 批改≤2 = 默认 3）。
    触顶 → 否决再抓帧/再调视觉，走 ABORT（维持现场景，控成本 C8）。
    阈值只从 config 读；护栏无开关、planner 不可覆盖。
    """
    budget = int(_orch(cfg)["vision_budget_per_problem"])
    if vision_calls_done >= budget:
        return GuardVerdict(
            allowed=False,
            degradation=Degradation.ABORT,
            reason="vision_budget_exhausted",
            detail={"vision_calls_done": vision_calls_done, "vision_budget": budget},
        )
    return GuardVerdict(allowed=True, reason="vision_budget_ok",
                        detail={"vision_calls_done": vision_calls_done, "vision_budget": budget})


def sticky_fallback(current_focus: Optional[str], planner_ok: bool, cfg: dict) -> GuardVerdict:
    """粘滞兜底（契约八 / PRD §7.4）：planner 超时/失败 → 维持当前 focus（不漂移）。

    planner_ok=False（软超时 planner_timeout_ms 触发 / planner 异常）→ 否决「换场景」，
    维持 current_focus 不变，走 FALLBACK_TEXT 填充语；focus 透传在 detail，供编排维持现场景。
    planner_ok=True → 放行 planner 的（可能换 focus 的）提议。

    本门不持有 planner 实例（铁律 5）：只接收 planner 是否成功的**事实布尔**，
    由编排层在 await planner 后用 timeout 结果填入；护栏据此确定性裁决，自身不调 planner。
    """
    # planner_timeout_ms 归属 roles.planner（编排层据此判 planner_ok），本门不读它，
    # 只对「事实成功/失败」做确定性裁决——立场与时序确定（铁律 6）。
    if not planner_ok:
        return GuardVerdict(
            allowed=False,
            degradation=FALLBACK_TEXT,
            reason="planner_unavailable_sticky",
            detail={"sticky_focus": current_focus},
        )
    return GuardVerdict(allowed=True, reason="planner_ok",
                        detail={"sticky_focus": current_focus})


def answer_guard(candidate_text, memory, cfg):
    """答案护栏（循环外，默认关）：正则组合拦截 solved_answer。M4 实现。

    契约九：唯一带 config 开关（answer_guard.enabled，demo 默认关）的护栏；触发词/数值
    组合正则由 E 提供。即便开关存在，**拦截层仍在循环外、planner 不可覆盖**（铁律 5）。
    """
    raise NotImplementedError("M4 加固")
