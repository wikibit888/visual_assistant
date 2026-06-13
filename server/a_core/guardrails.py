"""A · 确定性护栏（agent 不可覆盖；契约三/八/九；PRD §7.4）。M0 骨架。

全部为确定性代码，**在编排循环之外**，planner 不可覆盖、提示词不可关闭：
  - confidence_gate   视觉 confidence < config 阈 → 不播报错误/不开讲，改请口述
  - clarify_max       每 focus 澄清 ≤1
  - loop 上限          每回合工具往返 ≤ max_tool_rounds，触顶 FALLBACK_TEXT
  - vision_budget     单题视觉 ≤ vision_budget_per_problem（识题1+批改≤2）
  - sticky 兜底        planner 超时/失败 → 维持 focus
  - answer_guard      出站前正则组合拦截（可选，默认关；契约九）
护栏盲区（PRD §7.4）：只守「看清/时序/失控」，守不住「LLM 讲错」（语义层），知情接受。
"""

# from contracts import Degradation, GuardDecision


def confidence_gate(vision_result, threshold):
    """置信门控：低于阈值返回兜底指令。M2 实现。"""
    raise NotImplementedError("M2")


def answer_guard(candidate_text, memory, cfg):
    """答案护栏（循环外，默认关）：正则组合拦截 solved_answer。M4 实现。"""
    raise NotImplementedError("M4 加固")
