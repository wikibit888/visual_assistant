"""M2-03/04 后续验收：tool_registry 真实绑定 observe + memory_*（经 dispatch 透传 store）。

A 的注册表职责——把 contracts.TOOL_REGISTRY（schema 真理来源）绑定到可调用实现：
  - observe（M2-04）→ C 的真实实现（MOCK_VISION=1 读 fixture，零网络，契约六）。
  - memory_note/recall（M2-03）→ 会话级 WorkingMemoryStore 的 bound 协程方法（进程内恒可独立）。
缺 store 的 memory_* 须清晰报错（编排/会话层经 dispatch(..., store=...) 注入）。
协程用 asyncio.run 驱动（无 pytest-asyncio，与既有测试风格一致）。
"""

import asyncio

import pytest

from contracts.orchestration import ToolCall, ToolName
from contracts.vision import ObserveResult
from server.a_core import dispatch, tool_registry
from server.a_core.working_memory_store import WorkingMemoryStore


@pytest.fixture
def mock_vision(monkeypatch):
    """MOCK_VISION=1：observe 读 fixture、零网络（契约六）。"""
    monkeypatch.setenv("MOCK_VISION", "1")


# ── observe 真实绑定（无状态工具，忽略 store）─────────────────────────────────

def test_get_tool_observe_is_real_callable(mock_vision):
    """get_tool(observe) 绑定 C 真实实现（非 mock 桩），await 出合规 ObserveResult。"""
    fn = tool_registry.get_tool(ToolName.OBSERVE)
    result = asyncio.run(fn())
    assert isinstance(result, ObserveResult)
    assert result.description.strip()            # 真 observe（fixture 首条 payload），描述非空
    # 桩形态是 {"tool": ..., "mock": True}——绑定真实后不应再是 dict。
    assert not isinstance(result, dict)


def test_get_tool_observe_ignores_store(mock_vision):
    """observe 无状态：传不传 store 都返回同一真实实现（store 仅 memory_* 用）。"""
    fn = tool_registry.get_tool(ToolName.OBSERVE, store=WorkingMemoryStore())
    result = asyncio.run(fn())
    assert isinstance(result, ObserveResult)
    assert result.description.strip()


def test_dispatch_observe_passthrough_no_store(mock_vision):
    """dispatch(observe_call) 不传 store 也正常贯通（无状态工具）。"""
    result = asyncio.run(dispatch.dispatch(ToolCall(name=ToolName.OBSERVE)))
    assert isinstance(result, ObserveResult)
    assert result.description.strip()


# ── memory_* 真实绑定（需会话级 store）───────────────────────────────────────

def test_memory_roundtrip_via_dispatch():
    """经 dispatch 往返：note 写 k=v → recall(k) 取回 v（绑定同一 store 的 bound 方法）。"""
    store = WorkingMemoryStore()

    async def _roundtrip():
        await dispatch.dispatch(
            ToolCall(name=ToolName.MEMORY_NOTE, args={"key": "k", "value": "v"}),
            store=store,
        )
        return await dispatch.dispatch(
            ToolCall(name=ToolName.MEMORY_RECALL, args={"key": "k"}),
            store=store,
        )

    assert asyncio.run(_roundtrip()) == "v"


def test_get_tool_memory_note_bound_to_store():
    """get_tool(memory_note, store) 返回 store 的 bound 协程方法（同一实例往返可见）。"""
    store = WorkingMemoryStore()
    note = tool_registry.get_tool(ToolName.MEMORY_NOTE, store=store)
    recall = tool_registry.get_tool(ToolName.MEMORY_RECALL, store=store)
    assert note == store.memory_note          # bound 方法，绑定到注入的 store
    assert recall == store.memory_recall

    async def _run():
        await note(key="x", value=42)
        return await recall(key="x")

    assert asyncio.run(_run()) == 42


def test_memory_recall_missing_key_returns_none():
    """recall 未 note 过的 key → None（契约十 out_schema=(value)，缺失返回 None）。"""
    store = WorkingMemoryStore()
    result = asyncio.run(
        dispatch.dispatch(ToolCall(name=ToolName.MEMORY_RECALL, args={"key": "nope"}), store=store)
    )
    assert result is None


# ── memory_* 缺 store：清晰报错（编排/会话层须注入）───────────────────────────

def test_get_tool_memory_note_without_store_raises_valueerror():
    """get_tool(memory_note) 无 store → ValueError（清晰指引经 dispatch(..., store=...) 注入）。"""
    with pytest.raises(ValueError):
        tool_registry.get_tool(ToolName.MEMORY_NOTE)


def test_get_tool_memory_recall_without_store_raises_valueerror():
    """get_tool(memory_recall) 无 store → ValueError（同上）。"""
    with pytest.raises(ValueError):
        tool_registry.get_tool(ToolName.MEMORY_RECALL)


def test_dispatch_memory_without_store_raises_valueerror():
    """dispatch(memory_call) 不传 store → 透传 None → get_tool 抛 ValueError（守底）。"""
    with pytest.raises(ValueError):
        asyncio.run(
            dispatch.dispatch(ToolCall(name=ToolName.MEMORY_NOTE, args={"key": "k", "value": "v"}))
        )


# ── 越界守底不变 ─────────────────────────────────────────────────────────────

def test_out_of_whitelist_still_keyerror_with_store():
    """越界工具名即便带 store 也仍 KeyError（白名单守底不被 store 形参绕过）。"""
    with pytest.raises(KeyError):
        tool_registry.get_tool("delete_everything", store=WorkingMemoryStore())
