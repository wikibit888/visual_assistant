"""M2-03 验收：A 工作记忆运行时 + memory_note/recall（契约十，**仅内存、绝不落盘**）。

零外部服务 / 零 LLM / 无 MOCK 开关：WorkingMemoryStore 是进程内对象，恒可独立运行。
验证：
  (a) note→recall 往返正确；同 key 覆盖写取最新；缺失 key recall 返回 None。
  (b) 入参经契约十 MemoryNoteArgs / MemoryRecallArgs 校验（非法入参被拒）。
  (c) 结构化 store.memory 可访问（默认 WorkingMemory，current_mode=open）。
  (d) **绝不落盘**：monkeypatch builtins.open 抛错后 note/recall 仍正常；store 无持久化路径属性。
memory_note/recall 是协程；无 pytest-asyncio，用 asyncio.run 驱动（与既有测试一致）。
"""

import asyncio

import pytest
from pydantic import ValidationError

from contracts.orchestration import Mode
from contracts.working_memory import WorkingMemory
from server.a_core.working_memory_store import WorkingMemoryStore


# ── (a) note→recall 往返 / 覆盖写 / 缺失 key ──────────────────────────────────

def test_note_then_recall_round_trip():
    """note 写入 → recall 取回同一 value（往返一致）。"""
    store = WorkingMemoryStore()
    ack = asyncio.run(store.memory_note("favorite", "蓝色"))
    # out_schema = (ack)：ack 形态，回带 key。
    assert ack == {"ok": True, "key": "favorite"}
    assert asyncio.run(store.memory_recall("favorite")) == "蓝色"


def test_note_overwrite_same_key_returns_latest():
    """同 key 再 note → recall 取最新值（覆盖写，不累积旧值）。"""
    store = WorkingMemoryStore()
    asyncio.run(store.memory_note("focus", "第一题"))
    asyncio.run(store.memory_note("focus", "第二题"))
    assert asyncio.run(store.memory_recall("focus")) == "第二题"


def test_recall_missing_key_returns_none():
    """缺失 key → recall 返回 None（out_schema = (value)，无则 None）。"""
    store = WorkingMemoryStore()
    assert asyncio.run(store.memory_recall("never_set")) is None


def test_note_value_defaults_to_none():
    """value 省略 → 默认 None；recall 取回 None（与「缺失」区分：key 已登记）。"""
    store = WorkingMemoryStore()
    asyncio.run(store.memory_note("flag"))
    assert asyncio.run(store.memory_recall("flag")) is None


def test_note_accepts_non_str_value():
    """value: Any（契约十）——可存非字符串（dict / int 等），原样取回。"""
    store = WorkingMemoryStore()
    payload = {"line": 3, "type": "符号错误"}
    asyncio.run(store.memory_note("last_mistake", payload))
    assert asyncio.run(store.memory_recall("last_mistake")) == payload


def test_notes_are_isolated_per_instance():
    """两个 store 实例的 KV 互不串（单会话隔离，非共享类属性）。"""
    a = WorkingMemoryStore()
    b = WorkingMemoryStore()
    asyncio.run(a.memory_note("k", "A 的值"))
    assert asyncio.run(b.memory_recall("k")) is None


# ── (b) 入参经契约十校验（非法被拒）──────────────────────────────────────────

def test_note_rejects_non_str_key():
    """key 必须是 str（MemoryNoteArgs）——非 str（如 None）被拒。"""
    store = WorkingMemoryStore()
    with pytest.raises(ValidationError):
        asyncio.run(store.memory_note(None, "v"))


def test_recall_rejects_non_str_key():
    """recall 入参同样经 MemoryRecallArgs 校验——非 str key 被拒。"""
    store = WorkingMemoryStore()
    with pytest.raises(ValidationError):
        asyncio.run(store.memory_recall(123))


def test_note_key_coercion_matches_contract():
    """入参由契约十模型裁决：合法 str key 正常通过（不在 store 层另立校验）。"""
    store = WorkingMemoryStore()
    # 纯数字字符串是合法 str → 通过（验证校验源是契约模型，非 store 自定规则）。
    asyncio.run(store.memory_note("42", "ok"))
    assert asyncio.run(store.memory_recall("42")) == "ok"


# ── (c) 结构化 store.memory 可访问 ───────────────────────────────────────────

def test_structured_memory_default_is_open_mode():
    """store.memory 是默认 WorkingMemory；current_mode 默认 open（契约十/八 sticky 基座）。"""
    store = WorkingMemoryStore()
    assert isinstance(store.memory, WorkingMemory)
    assert store.memory.current_mode == Mode.OPEN
    assert store.memory.active_problem is None
    assert store.memory.mistake_log == []
    assert store.memory.reminder_count == 0
    assert store.memory.clarify_count == 0


def test_structured_memory_is_writable_by_orchestrator():
    """编排器可读写结构化字段（如切 mode）——store 不拦结构化写。"""
    store = WorkingMemoryStore()
    store.memory.current_mode = Mode.LEARNING
    assert store.memory.current_mode == Mode.LEARNING


def test_structured_memory_and_kv_are_separate():
    """结构化记忆与通用 KV 是两套（KV 不污染 WorkingMemory 模型字段）。"""
    store = WorkingMemoryStore()
    asyncio.run(store.memory_note("current_mode", "伪装成字段名的 KV"))
    # KV 写入不改结构化 current_mode（仍是默认 open）。
    assert store.memory.current_mode == Mode.OPEN
    # WorkingMemory 模型未被偷偷加 KV 字段（契约十零改动）。
    assert "current_mode" not in store.memory.model_dump().get("_notes", {})
    assert not hasattr(store.memory, "_notes")


# ── (d) 绝不落盘（隐私基线，PRD §1.5 / §7.7）─────────────────────────────────

def test_no_persistence_path_attributes():
    """store 无任何持久化路径 / 文件句柄属性（仅内存 self.memory + self._notes）。"""
    store = WorkingMemoryStore()
    public_attrs = {a for a in vars(store)}
    assert public_attrs == {"memory", "_notes"}
    # KV 是纯内存 dict，不是文件类对象。
    assert isinstance(store._notes, dict)
    for forbidden in ("path", "file", "fp", "db", "conn", "fd"):
        assert not hasattr(store, forbidden)


def test_works_with_open_disabled(monkeypatch):
    """断 open()：monkeypatch builtins.open 抛错后，构造 + note/recall 仍正常 → 证实零落盘。"""
    def _boom(*_args, **_kwargs):
        raise AssertionError("绝不落盘：working_memory_store 不得调用 open()")

    monkeypatch.setattr("builtins.open", _boom)
    # 构造（含默认结构化记忆）不碰文件。
    store = WorkingMemoryStore()
    # note / recall 全程不碰文件。
    asyncio.run(store.memory_note("k", "v"))
    assert asyncio.run(store.memory_recall("k")) == "v"
    assert asyncio.run(store.memory_recall("missing")) is None


# ── 会话结束丢弃（discard 清空，仅内存）──────────────────────────────────────

def test_discard_clears_memory_and_notes():
    """discard() 清空结构化记忆 + KV（会话结束归零；无文件需清理）。"""
    store = WorkingMemoryStore()
    asyncio.run(store.memory_note("k", "v"))
    store.memory.current_mode = Mode.LIFE

    store.discard()

    assert asyncio.run(store.memory_recall("k")) is None
    assert store.memory.current_mode == Mode.OPEN  # 复位为默认 WorkingMemory
