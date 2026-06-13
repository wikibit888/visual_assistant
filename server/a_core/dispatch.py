"""A · 工具分发 + rails 注入 hook（契约八；PRD §7.2）。M0 骨架。

agentic：按 PlannerOutput.tools 顺序 dispatch，受 max_tool_rounds 约束。
rails：dispatch 前置注入 config.rails.forced_tool_sequence（工具序 + answer 节点混合），
       max_tool_rounds→0。M0 要求此注入做成可切换 hook（+0.5h），M2 零成本切换。
"""


def resolve_sequence(mode, cfg):
    """据 config 决定本回合执行序：agentic（planner 自由）或 rails（强制序）。M2 实现。"""
    raise NotImplementedError("M2：rails 可注入 hook 已在 config 就位，此处实现切换")


async def dispatch(tool_call):
    """执行单个工具（经 tool_registry → 真实/ MOCK 实现）。M2 实现。"""
    raise NotImplementedError("M2")
