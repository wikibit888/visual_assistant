"""A · 反应式 agent 编排循环（契约八；PRD §7.2 伪码见 §1.4）。M0 骨架。

每个 asr.final 触发一次循环：
  1. 感知 asr.final + 工作记忆 → 组 planner 输入
  2. planner LLM（deepseek-chat，温度0+结构化）→ PlannerOutput{kind, mode, tools, text}
     （MOCK_PLANNER=1 走固定脚本，不调 LLM）
  3. kind==answer → 直接候选回复（快路径）
     kind==tool_calls → dispatch（≤max_tool_rounds）→ 组合 → 候选回复
     kind==clarify → 澄清（受 clarify_max）
  4. 候选回复 → 确定性护栏裁决（guardrails）→ tts.say
超时（planner_timeout_ms）/触顶（max_tool_rounds）→ 维持现场景 + FALLBACK_TEXT。
"""

# from contracts import PlannerOutput, WorkingMemory


async def run_turn(asr_final, memory):
    """处理一个用户回合，产出经护栏裁决的回复序列。M2 实现。"""
    raise NotImplementedError("M2 主场景合体：编排循环")


async def call_planner(planner_input):
    """调 planner（结构化输出）；MOCK_PLANNER=1 返回固定脚本。M2 实现。"""
    raise NotImplementedError("M2")
