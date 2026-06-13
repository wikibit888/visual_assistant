"""M2 活体接线 · 闭环集成：A 的 Session 在 asr.final → 编排 loop → tts.say 上真实跑通。

零外部服务 / 零 LLM（全离线，契约六）：MOCK_PLANNER 走固定脚本、MOCK_VISION 读 fixture。
验证（本批新增）：
  1. Session.next_turn_id() 连内唯一递增（t-000001 → t-000002，格式 8 字符），turn_id 归 A。
  2. handle_asr_final → 返回 Envelope(tts.say, voice)，turn_id 与入参一致、payload TtsSay 非空
     （A 只发 tts.say 给 B，铁律2；经护栏闸门收口，铁律3/5）。
  3. mode 写回（契约八 sticky）：识到「题」→ 编排把 store.memory.current_mode 写为 learning。
  4. store 经编排 loop 贯通到 memory_* 工具：monkeypatch call_planner 产 memory_note 工具回合，
     handle 后从同一 session.store recall 出该 value（证明 store 经 run_turn→dispatch→get_tool 注入）。

run_turn / handle_asr_final / memory_* 是协程；无 pytest-asyncio，用 asyncio.run 驱动
（与既有测试风格一致）。
"""

import asyncio

import pytest

from contracts.orchestration import (
    Mode,
    PlannerKind,
    PlannerOutput,
    ToolCall,
    ToolName,
)
from contracts.voice import AsrFinal
from server.a_core import orchestrator
from server.a_core.session import Session


@pytest.fixture
def mocks(monkeypatch):
    """MOCK_PLANNER + MOCK_VISION：脱依赖、可独立空跑（契约六，全离线零网络）。"""
    monkeypatch.setenv("MOCK_PLANNER", "1")
    monkeypatch.setenv("MOCK_VISION", "1")


def _asr(text: str, turn_id: str = "t-000001") -> AsrFinal:
    return AsrFinal(text=text, confidence=0.9, turn_id=turn_id)


def _cfg(max_tool_rounds: int = 2, rails_enabled: bool = False) -> dict:
    """注入式最小 config（禁硬编码：被测代码从此 dict 读 max_tool_rounds，不读字面量）。"""
    return {
        "orchestration": {"max_tool_rounds": max_tool_rounds},
        "rails": {"enabled": rails_enabled, "forced_tool_sequence": {}},
    }


# ── 1. turn_id 由 A 分配，连内唯一递增 ──────────────────────────────────────────

def test_next_turn_id_increments_within_session():
    """Session.next_turn_id() 连内递增（t-000001 → t-000002），格式 t-NNNNNN（8 字符）。"""
    session = Session(_cfg())
    first = session.next_turn_id()
    second = session.next_turn_id()
    assert first == "t-000001"
    assert second == "t-000002"
    assert len(first) == 8 and first.startswith("t-")
    assert first != second


# ── 2. asr.final → 编排 loop → tts.say 信封下行 ─────────────────────────────────

def test_handle_asr_final_yields_tts_say_envelope(mocks):
    """handle_asr_final → [Envelope]，type=tts.say、channel=voice、turn_id 一致、TtsSay 非空。"""
    session = Session(_cfg())
    outs = asyncio.run(session.handle_asr_final(_asr("你好呀", turn_id="t-000007"), _cfg()))

    assert isinstance(outs, list) and len(outs) == 1
    env = outs[0]
    # A 只发 tts.say 给 B（铁律2）；voice 通道；同回合共享入参 turn_id（契约一）。
    assert env.type.value == "tts.say"
    assert env.channel.value == "voice"
    assert env.turn_id == "t-000007"
    # payload 合规 TtsSay：候选文本经护栏闸门收口，非空。
    assert env.payload["text"].strip()
    assert env.payload["turn_id"] == "t-000007"


# ── 3. mode 写回（契约八 sticky）─────────────────────────────────────────────────

def test_mode_written_back_to_working_memory(mocks):
    """识到「题」→ MOCK 脚本 mode=learning → 编排把 store.memory.current_mode 写回 learning。"""
    session = Session(_cfg())
    # 写回前默认 open。
    assert session.store.memory.current_mode == Mode.OPEN
    asyncio.run(session.handle_asr_final(_asr("看看这道题"), _cfg()))
    # planner 裁定 learning → 写入工作记忆，供下一回合 sticky。
    assert session.store.memory.current_mode == Mode.LEARNING


def test_open_text_keeps_mode_open(mocks):
    """无特征文本 → MOCK 脚本 answer/mode=open → current_mode 维持 open（写回幂等）。"""
    session = Session(_cfg())
    asyncio.run(session.handle_asr_final(_asr("你好呀"), _cfg()))
    assert session.store.memory.current_mode == Mode.OPEN


# ── 4. store 经编排 loop 贯通到 memory_* 工具 ───────────────────────────────────

def test_store_threads_through_loop_into_memory_tool(mocks, monkeypatch):
    """monkeypatch call_planner 产 memory_note 工具回合 → handle 后从同一 store recall 出 value。

    证明会话级 store 经 run_turn → dispatch(..., store=store) → get_tool 注入到 memory_* 工具
    （memory_note 绑定的是本 session.store 的 bound 协程方法，写入同一容器）。
    """
    session = Session(_cfg())

    async def _fake_planner(planner_input, cfg=None):
        # 工具回合：memory_note 写入一对 KV；mode=open（不触 sticky 学习/生活分支）。
        return PlannerOutput(
            kind=PlannerKind.TOOL_CALLS,
            mode=Mode.OPEN,
            tools=[ToolCall(name=ToolName.MEMORY_NOTE, args={"key": "fav", "value": "蓝色"})],
            text=None,
        )

    monkeypatch.setattr(orchestrator, "call_planner", _fake_planner)

    # max_tool_rounds=1：memory_note 在 cap 内执行（写入 store）。
    asyncio.run(session.handle_asr_final(_asr("记一下"), _cfg(max_tool_rounds=1)))

    # 从同一 session.store recall：拿到工具写入的 value → store 确经 loop 贯通到工具。
    recalled = asyncio.run(session.store.memory_recall("fav"))
    assert recalled == "蓝色"
