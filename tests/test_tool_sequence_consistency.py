"""M2-06 · 工具序一致性初测（契约八；MOCK_PLANNER 确定性路径）。

验「同动线 → 工具调用序列一致」——口径是**工具序一致，非逐字一致**（text 措辞可变、
工具名序列必须稳定）。本初测用 MOCK_PLANNER 的确定性脚本建立一致性校验脚手架；
真 LLM（温度0）的完整一致性专测留 M7 彩排（PRD §10/§11 工具序一致性）。

注：初测用 `MOCK_PLANNER`（确定性 planner 脚本）取代 TASKS 表标的 `MOCK_LLM`——
后者会让 planner 走真路径桩、落兜底，无法产稳定工具序；确定性脚本才是一致性脚手架的正确底座。
"""

import asyncio

import pytest

from contracts.orchestration import PlannerKind
from server.a_core import orchestrator


@pytest.fixture
def mock_planner(monkeypatch):
    """MOCK_PLANNER：planner 走确定性脚本（同输入→同结构化输出，零网络，契约六）。"""
    monkeypatch.setenv("MOCK_PLANNER", "1")


# 代表性动线 → 期望工具名序列（比较的是工具序，不是 text）。
DONGXIAN = [
    ("看看这道题", ["read_problem"]),                       # learning 单工具
    ("用多工具处理一下", ["observe", "read_problem", "check_draft"]),  # 多工具序
    ("你好呀", []),                                          # answer 快路径零工具
    ("这是什么意思？", []),                                  # clarify 零工具
]

N = 5  # 同动线重复次数


def _tool_seq(text: str):
    plan = asyncio.run(orchestrator.call_planner({"text": text}))
    return plan.kind, tuple(t.name.value for t in plan.tools)


@pytest.mark.parametrize("text,expected_tools", DONGXIAN)
def test_same_dongxian_yields_consistent_tool_sequence(mock_planner, text, expected_tools):
    """同一动线重复 N 次 → 工具调用序列完全一致，且与期望一致；kind 也一致。"""
    runs = [_tool_seq(text) for _ in range(N)]
    seqs = {seq for _, seq in runs}
    kinds = {k for k, _ in runs}
    assert len(seqs) == 1, f"工具序不一致：{seqs}"
    assert len(kinds) == 1, f"kind 不一致：{kinds}"
    assert list(seqs.pop()) == expected_tools


def test_consistency_is_about_sequence_not_text(mock_planner):
    """一致性口径 = 工具序一致，不要求 text 逐字一致（前瞻真 LLM：措辞可变、工具序稳）。

    固化「比较的是工具序、不是 text」的判定，供 M7 真 LLM 专测复用同一口径。
    """
    text = "用多工具处理一下"
    plans = [asyncio.run(orchestrator.call_planner({"text": text})) for _ in range(3)]
    tool_seqs = {tuple(t.name.value for t in p.tools) for p in plans}
    assert len(tool_seqs) == 1  # 工具序稳定（text 不参与一致性判定）
    assert all(p.kind == PlannerKind.TOOL_CALLS for p in plans)
