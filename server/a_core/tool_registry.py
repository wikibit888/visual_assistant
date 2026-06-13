"""A · 工具注册表运行时（契约八 / §7.3）。M1-02 骨架级接线。

绑定 contracts.TOOL_REGISTRY（schema 真理来源）→ 可调用实现（真实 / MOCK）：
  read_problem/check_draft/observe → C（server.c_vision），MOCK_VISION=1 读 fixture
  weather_get                      → weather 工具，MOCK_WEATHER=1 写死兜底
  memory_note/recall               → working_memory_store（进程内，恒可独立）
「说话」非工具；posture.* 不进表；无执行类工具（越界 → 帮不上）。

get_tool 仅认 contracts.TOOL_REGISTRY 收录的工具名（白名单单一真理来源）。
真实绑定（A 的注册表职责）：
- read_problem（M1-08）/ observe（M2-04）→ 直接绑定 C 已落地的真实实现（其内部按 MOCK_VISION 脱依赖）。
- memory_note / memory_recall（M2-03）→ 绑定会话级 WorkingMemoryStore 的 bound 协程方法；
  须经 get_tool(..., store=...)（由 dispatch 透传）注入 store，缺 store 即清晰 ValueError。
- 其余工具真实实现尚未落地（check_draft=M3-02 / weather=M4）：**仅当该工具声明了 mock_env
  且对应 MOCK_X=1 时**，回落到一个极薄的 MOCK 协程（返回 ack 形态 dict），让编排骨架可端到端
  空跑。非 MOCK 路径仍按真实实现，真实实现未就位则显式 NotImplementedError——不静默假成功
  （不污染后续真接线）。
"""

from __future__ import annotations

from typing import Awaitable, Callable

from contracts.orchestration import TOOL_REGISTRY, ToolName
from contracts.mock import is_mock


def _real_read_problem() -> Callable[..., Awaitable]:
    """C 的真实 read_problem（其内部已按 MOCK_VISION 脱依赖，契约六）。"""
    from server.c_vision import vision_tools

    return vision_tools.read_problem


def _real_observe() -> Callable[..., Awaitable]:
    """C 的真实 observe（其内部已按 MOCK_VISION 脱依赖，契约六）。镜像 read_problem。"""
    from server.c_vision import vision_tools

    return vision_tools.observe


def _mock_stub(name: str) -> Callable[..., Awaitable]:
    """极薄 MOCK 协程：返回 ack 形态 dict，供编排骨架空跑（M1-02）。

    仅用于尚未落地真实实现、但 MOCK_X=1 的工具——让 loop+dispatch 链路贯通；
    真实形态（契约三/四 等）由各工具自己的批次落地，本桩不冒充其 schema。
    """

    async def _stub(*_args, **_kwargs):
        return {"tool": name, "mock": True, "ack": True}

    return _stub


def get_tool(name, store=None) -> Callable[..., Awaitable]:
    """按 ToolName 取可调用实现（真实或 MOCK）。

    name：contracts.ToolName 或其 .value 字符串。只认白名单（TOOL_REGISTRY）内工具；
    越界（不在注册表）→ KeyError（planner 工具选择越界由护栏/校验拦，此处守底）。
    store：会话级 WorkingMemoryStore（仅 memory_* 需要——绑定其 bound 协程方法）；
      无状态工具（read_problem/observe/...）忽略此参。经 dispatch(..., store=...) 注入。
    返回值统一是「协程函数」（dispatch 以 await 调用）。
    """
    key = name.value if isinstance(name, ToolName) else str(name)
    spec = TOOL_REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"工具不在白名单（契约八 TOOL_REGISTRY）：{key}")

    # read_problem 已有真实实现（M1-08），直接绑定（其内部按 MOCK_VISION 脱依赖）。
    if key == ToolName.READ_PROBLEM.value:
        return _real_read_problem()

    # observe 已有真实实现（M2-04），直接绑定（其内部按 MOCK_VISION 脱依赖）。
    if key == ToolName.OBSERVE.value:
        return _real_observe()

    # memory_*：绑定会话级 WorkingMemoryStore 的 bound 协程方法（进程内，恒可独立）。
    # 无 store 即无会话上下文——清晰报错（编排/会话层须经 dispatch(..., store=...) 注入）。
    if key in (ToolName.MEMORY_NOTE.value, ToolName.MEMORY_RECALL.value):
        if store is None:
            raise ValueError(
                f"工具 {key} 需会话级 WorkingMemoryStore（请经 dispatch(..., store=...) 注入）"
            )
        return (
            store.memory_note
            if key == ToolName.MEMORY_NOTE.value
            else store.memory_recall
        )

    # 其余工具真实实现尚未落地：MOCK_X=1 时回落极薄桩，让编排骨架可空跑（M1-02）。
    if spec.mock_env is not None and is_mock(spec.mock_env):
        return _mock_stub(key)

    # 真实实现未就位且无 MOCK 兜底 → 显式 NotImplementedError（不静默假成功）。
    raise NotImplementedError(
        f"工具 {key} 真实实现尚未落地（见 contracts.TOOL_REGISTRY out_schema 标注批次）"
    )
