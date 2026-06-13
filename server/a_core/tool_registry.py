"""A · 工具注册表运行时（契约八 / §7.3）。M0 骨架。

绑定 contracts.TOOL_REGISTRY（schema）→ 真实/ MOCK 实现：
  read_problem/check_draft/observe → C（server.c_vision），MOCK_VISION=1 读 fixture
  weather_get                      → weather 工具，MOCK_WEATHER=1 写死兜底
  memory_note/recall               → working_memory_store（进程内，恒可独立）
「说话」非工具；posture.* 不进表；无执行类工具（越界 → 帮不上）。
"""

# from contracts import TOOL_REGISTRY, ToolName


def get_tool(name):
    """按 ToolName 取可调用实现（真实或 MOCK）。M2 实现。"""
    raise NotImplementedError("M2")
