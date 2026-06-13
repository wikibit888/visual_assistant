"""A · 工具注册表运行时（契约八 / §7.3）。M1-02 骨架级接线。

绑定 contracts.TOOL_REGISTRY（schema 真理来源）→ 可调用实现（真实 / MOCK）：
  read_problem/check_draft/observe → C（server.c_vision），MOCK_VISION=1 读 fixture
  weather_get                      → weather 工具，MOCK_WEATHER=1 写死兜底
  memory_note/recall               → working_memory_store（进程内，恒可独立）
「说话」非工具；posture.* 不进表；无执行类工具（越界 → 帮不上）。

本批（M1-02）只做「loop+dispatch 空跑」：
- get_tool 仅认 contracts.TOOL_REGISTRY 收录的工具名（白名单单一真理来源）。
- read_problem 直接绑定 C 已落地的真实实现（其内部按 MOCK_VISION 脱依赖）。
- 其余工具真实实现尚未落地（observe=M2 / check_draft=M3 / weather=M4 / memory=M2；里程碑号按「基座→收窄」重排）。
  为让编排骨架可端到端空跑，**仅当该工具声明了 mock_env 且对应 MOCK_X=1 时**，
  回落到一个极薄的 MOCK 协程（返回 ack 形态 dict）。非 MOCK 路径仍按真实实现，
  真实实现未就位则显式 NotImplementedError——不静默假成功（不污染后续真接线）。
"""

from __future__ import annotations

from typing import Awaitable, Callable

from contracts.orchestration import TOOL_REGISTRY, ToolName
from contracts.mock import is_mock


def _real_read_problem() -> Callable[..., Awaitable]:
    """C 的真实 read_problem（其内部已按 MOCK_VISION 脱依赖，契约六）。"""
    from server.c_vision import vision_tools

    return vision_tools.read_problem


def _mock_stub(name: str) -> Callable[..., Awaitable]:
    """极薄 MOCK 协程：返回 ack 形态 dict，供编排骨架空跑（M1-02）。

    仅用于尚未落地真实实现、但 MOCK_X=1 的工具——让 loop+dispatch 链路贯通；
    真实形态（契约三/四 等）由各工具自己的批次落地，本桩不冒充其 schema。
    """

    async def _stub(*_args, **_kwargs):
        return {"tool": name, "mock": True, "ack": True}

    return _stub


def get_tool(name) -> Callable[..., Awaitable]:
    """按 ToolName 取可调用实现（真实或 MOCK）。

    name：contracts.ToolName 或其 .value 字符串。只认白名单（TOOL_REGISTRY）内工具；
    越界（不在注册表）→ KeyError（planner 工具选择越界由护栏/校验拦，此处守底）。
    返回值统一是「协程函数」（dispatch 以 await 调用）。
    """
    key = name.value if isinstance(name, ToolName) else str(name)
    spec = TOOL_REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"工具不在白名单（契约八 TOOL_REGISTRY）：{key}")

    # read_problem 已有真实实现（M1-08），直接绑定（其内部按 MOCK_VISION 脱依赖）。
    if key == ToolName.READ_PROBLEM.value:
        return _real_read_problem()

    # 其余工具真实实现尚未落地：MOCK_X=1 时回落极薄桩，让编排骨架可空跑（M1-02）。
    if spec.mock_env is not None and is_mock(spec.mock_env):
        return _mock_stub(key)

    # 进程内工具（memory_*，mock_env=None）也尚未落地：留给 M2 接 working_memory_store。
    raise NotImplementedError(
        f"工具 {key} 真实实现尚未落地（见 contracts.TOOL_REGISTRY out_schema 标注批次）"
    )
