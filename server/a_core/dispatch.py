"""A · 工具分发 + rails 注入 hook（契约八；PRD §7.2）。M1-02 骨架级空跑。

agentic：按 PlannerOutput.tools 顺序 dispatch，受 max_tool_rounds 约束（截顶由编排循环施加）。
rails：dispatch 前置注入 config.rails.forced_tool_sequence（工具序 + answer 节点混合），
       max_tool_rounds→0。M0 要求此注入做成可切换 hook（+0.5h），M2 零成本切换。

本批（M1-02）落地范围：
- resolve_sequence：rails 可注入 hook 就位——config.rails.enabled=false 走 agentic
  （原样返回 planner 选的 tools 列表）；enabled=true 注入 forced_tool_sequence[mode] 的
  step==tool 节点（answer 节点不是工具、由编排循环处理，此处过滤掉）。
- dispatch：执行单个 ToolCall（经 tool_registry → 真实 / MOCK 实现），await 之。
真正的 rails 总开关接线 + 越界优雅收口在 M2（rails.is_railed / guardrails），本批不做。
"""

from __future__ import annotations

from server.a_core import tool_registry


def resolve_sequence(planner_tools, mode, cfg):
    """决定本回合的工具执行序，返回 ToolCall 列表（dispatch 逐个执行）。

    agentic（config.rails.enabled 非真）：原样返回 planner 选择的 tools（planner 自由）。
    rails（config.rails.enabled 为真）：忽略 planner 工具，注入 config 的
      forced_tool_sequence[mode] 中 step=="tool" 的节点（answer 节点非工具，由编排循环
      处理，不进 dispatch）。这是「工具序 + answer 节点混合」中工具序的提取（§7.2）。

    planner_tools：list[ToolCall]（kind=tool_calls 时非空）。
    mode：contracts.Mode 或其 .value 字符串（rails 序按 mode 取）。
    cfg：load_config() 结果（rails 段 + forced_tool_sequence 在此读，禁硬编码）。
    """
    rails_cfg = cfg.get("rails", {}) if cfg else {}
    if not rails_cfg.get("enabled", False):
        # agentic：planner 自由，原样透传其工具选择。
        return list(planner_tools or [])

    # rails：按 mode 取强制序，仅取 step=="tool" 节点（answer 节点交编排循环）。
    mode_key = getattr(mode, "value", mode)
    seq = (rails_cfg.get("forced_tool_sequence", {}) or {}).get(mode_key, []) or []
    # 延迟构造 ToolCall，避免 dispatch 外泄契约类型耦合到本骨架的非工具节点。
    from contracts.orchestration import ToolCall

    return [
        ToolCall(name=item["name"])
        for item in seq
        if (item.get("step") if isinstance(item, dict) else getattr(item, "step", None))
        == "tool"
    ]


async def dispatch(tool_call):
    """执行单个工具（经 tool_registry → 真实 / MOCK 实现），await 返回其结果。

    tool_call：contracts.ToolCall{name, args}。tool_registry.get_tool 据 name 取协程实现，
    以 args 关键字展开调用。MOCK_X=1 时取 MOCK 实现（脱依赖，契约六）。
    """
    fn = tool_registry.get_tool(tool_call.name)
    return await fn(**(tool_call.args or {}))
