"""M1-02 验收：A 编排器骨架（loop+dispatch 空跑，MOCK_PLANNER + MOCK_VISION）。

零外部服务 / 零 LLM：MOCK_PLANNER 走固定脚本、MOCK_VISION 读 fixture。
验证：
  1. asr.final → planner(脚本) → tts.say 空循环贯通（answer / clarify / tool_calls 三类 kind）。
  2. kind=tool_calls 受 config.orchestration.max_tool_rounds 截顶（触顶停，走 FALLBACK_TEXT）。
  3. 护栏调用位为单一出站闸门（_guardrail_decide 占位直通，本批不实现护栏体）。
run_turn / dispatch 是协程；无 pytest-asyncio，用 asyncio.run 驱动（与既有测试一致）。
"""

import asyncio

import pytest

from contracts.voice import AsrFinal, TtsSay
from contracts.orchestration import (
    Mode,
    PlannerKind,
    PlannerOutput,
    ToolCall,
    ToolName,
)
from contracts.working_memory import WorkingMemory
from server.a_core import orchestrator, dispatch, tool_registry


@pytest.fixture
def mocks(monkeypatch):
    """MOCK_PLANNER + MOCK_VISION：脱依赖、可独立空跑（契约六）。"""
    monkeypatch.setenv("MOCK_PLANNER", "1")
    monkeypatch.setenv("MOCK_VISION", "1")


def _asr(text: str, turn_id: str = "t-000001") -> AsrFinal:
    return AsrFinal(text=text, confidence=0.9, turn_id=turn_id)


def _cfg(max_tool_rounds: int = 2, rails_enabled: bool = False) -> dict:
    """注入式最小 config（禁硬编码：被测代码从此 dict 读 max_tool_rounds，不读字面量）。"""
    return {
        "orchestration": {"max_tool_rounds": max_tool_rounds},
        "rails": {
            "enabled": rails_enabled,
            "forced_tool_sequence": {
                "learning": [
                    {"step": "tool", "name": "read_problem"},
                    {"step": "answer", "name": "guide"},
                    {"step": "tool", "name": "check_draft"},
                ],
            },
        },
    }


# ── 1. 空循环贯通：三类 kind 都收口为 tts.say ────────────────────────────────

def test_answer_kind_loop_through_to_tts_say(mocks):
    """文本无特征 → planner 脚本走 answer 快路径 → 出 tts.say（贯通）。"""
    out = asyncio.run(
        orchestrator.run_turn(_asr("你好呀"), WorkingMemory(), cfg=_cfg())
    )
    assert isinstance(out, list) and len(out) == 1
    say = out[0]
    assert isinstance(say, TtsSay)
    assert say.turn_id == "t-000001"
    assert say.text.strip()  # 候选回复非空，经护栏闸门收口


def test_clarify_kind_loop_through_to_tts_say(mocks):
    """文本含「？」→ planner 脚本走 clarify → 出 tts.say（澄清文本）。"""
    out = asyncio.run(
        orchestrator.run_turn(_asr("这是什么意思？"), WorkingMemory(), cfg=_cfg())
    )
    assert len(out) == 1
    assert out[0].text.strip()


def test_tool_calls_kind_under_cap_dispatches_and_says(mocks):
    """含「题」→ tool_calls(read_problem)，1 工具 ≤ max_tool_rounds=2 → 正常贯通到 tts.say。"""
    out = asyncio.run(
        orchestrator.run_turn(_asr("看看这道题"), WorkingMemory(), cfg=_cfg(max_tool_rounds=2))
    )
    assert len(out) == 1
    say = out[0]
    assert isinstance(say, TtsSay)
    # 未触顶 → 候选回复非 FALLBACK_TEXT（plan.text 为 None → FALLBACK；但本脚本工具回合
    # text=None，故未触顶时也走 FALLBACK_TEXT 占位，断言仅验证出 tts.say 贯通）。
    assert say.text.strip()


# ── 2. max_tool_rounds 截顶（触顶停）─────────────────────────────────────────

def test_tool_calls_capped_by_max_tool_rounds(mocks):
    """含「多工具」→ planner 脚本排 3 工具；max_tool_rounds=2 → 触顶停，第 3 个不执行。

    用 monkeypatch 计数 dispatch.dispatch 实际被调次数，验证恰好等于 cap（截顶生效）。
    """
    calls = []
    real_dispatch = dispatch.dispatch

    async def _counting_dispatch(tool_call):
        calls.append(tool_call.name)
        return await real_dispatch(tool_call)

    # 在 orchestrator 引用的 dispatch 模块上替换，确保被测循环走计数版本。
    import server.a_core.orchestrator as orch_mod
    orch_mod.dispatch.dispatch = _counting_dispatch
    try:
        out = asyncio.run(
            orchestrator.run_turn(_asr("用多工具看看"), WorkingMemory(), cfg=_cfg(max_tool_rounds=2))
        )
    finally:
        orch_mod.dispatch.dispatch = real_dispatch

    # 脚本排 3 个工具，cap=2 → 只执行 2 个（触顶停）。
    assert len(calls) == 2
    # 触顶 → 候选回复走 FALLBACK_TEXT。
    assert out[0].text == orchestrator.FALLBACK_TEXT


def test_cap_zero_dispatches_nothing(mocks):
    """max_tool_rounds=0（rails 口径）→ 一个工具都不执行，直接触顶 FALLBACK。"""
    calls = []
    real_dispatch = dispatch.dispatch

    async def _counting_dispatch(tool_call):
        calls.append(tool_call.name)
        return await real_dispatch(tool_call)

    import server.a_core.orchestrator as orch_mod
    orch_mod.dispatch.dispatch = _counting_dispatch
    try:
        out = asyncio.run(
            orchestrator.run_turn(_asr("看看这道题"), WorkingMemory(), cfg=_cfg(max_tool_rounds=0))
        )
    finally:
        orch_mod.dispatch.dispatch = real_dispatch

    assert calls == []
    assert out[0].text == orchestrator.FALLBACK_TEXT


# ── 3. call_planner MOCK 脚本形态 + dispatch/tool_registry 接线 ───────────────

def test_call_planner_mock_returns_structured_output(mocks):
    """MOCK_PLANNER → 返回合规 PlannerOutput（结构化，与真 planner 同形）。"""
    plan = asyncio.run(orchestrator.call_planner({"text": "看看这道题"}))
    assert isinstance(plan, PlannerOutput)
    PlannerOutput.model_validate(plan.model_dump())  # 构造→序列化→再校验
    assert plan.kind == PlannerKind.TOOL_CALLS
    assert plan.mode == Mode.LEARNING
    assert [t.name for t in plan.tools] == [ToolName.READ_PROBLEM]


def test_resolve_sequence_agentic_passthrough():
    """agentic（rails.enabled=false）：resolve_sequence 原样透传 planner 工具选择。"""
    tools = [ToolCall(name=ToolName.READ_PROBLEM), ToolCall(name=ToolName.OBSERVE)]
    seq = dispatch.resolve_sequence(tools, Mode.LEARNING, _cfg(rails_enabled=False))
    assert [t.name for t in seq] == [ToolName.READ_PROBLEM, ToolName.OBSERVE]


def test_resolve_sequence_rails_injects_tool_nodes_only():
    """rails（enabled=true）：注入 forced_tool_sequence 的 step==tool 节点，answer 节点过滤掉。"""
    seq = dispatch.resolve_sequence([], Mode.LEARNING, _cfg(rails_enabled=True))
    # learning 序 = [tool:read_problem, answer:guide, tool:check_draft] → 取 2 个 tool 节点。
    assert [t.name for t in seq] == [ToolName.READ_PROBLEM, ToolName.CHECK_DRAFT]


def test_tool_registry_read_problem_is_real_callable(mocks):
    """get_tool(read_problem) 绑定 C 真实实现（MOCK_VISION 下读 fixture），await 出契约三结果。"""
    fn = tool_registry.get_tool(ToolName.READ_PROBLEM)
    result = asyncio.run(fn())
    # C 的 read_problem 在 MOCK_VISION=1 下返回 ReadProblemResult（problem_text 非空）。
    assert result.problem_text.strip()


def test_tool_registry_mock_stub_for_unimplemented(mocks):
    """get_tool(observe)：真实实现未落地 + MOCK_VISION=1 → 回落极薄 MOCK 桩（ack 形态）。"""
    fn = tool_registry.get_tool(ToolName.OBSERVE)
    result = asyncio.run(fn())
    assert result == {"tool": "observe", "mock": True, "ack": True}


def test_tool_registry_rejects_out_of_whitelist():
    """越界工具名（不在 contracts.TOOL_REGISTRY 白名单）→ KeyError（守底）。"""
    with pytest.raises(KeyError):
        tool_registry.get_tool("delete_everything")
