"""M1-03 验收：A 确定性护栏（server/a_core/guardrails.py）。

护栏是「确定性地板」（PRD §7.4 / 铁律 3/5/6）：
  * 全确定性、纯函数、无副作用、无 LLM/外部依赖（零 MOCK）。
  * 阈值只从 config 读，代码中不硬编码（契约七）→ 本测试用「自造 cfg」改阈值，
    护栏行为随 cfg 变 = 证明它读 config、未硬编码。
  * 护栏不持有 planner 实例、不接收「能关闭它的开关」→ 函数签名里没有这种参数（结构上不可覆盖）。
  * 护栏在编排循环之外 = 不 import orchestrator、可脱离循环独立调用裁决。

各护栏（confidence_gate / clarify / loop 上限 / vision_budget / 粘滞兜底）各有触发单测。
answer_guard 留 M4（仍 raise NotImplementedError）。
"""

import inspect

import pytest

from contracts.config_schema import load_config
from contracts.errors import Degradation
from contracts.vision import CheckDraftResult, Verdict, VisionKind
from server.a_core import guardrails as g


# ── 自造 cfg：阈值集中此处，与真实 config.yaml 解耦（证明护栏读 config 而非硬编码）──
def _cfg(**orch):
    base = {
        "confidence_gate": 0.6,
        "clarify_max": 1,
        "max_tool_rounds": 2,
        "vision_budget_per_problem": 3,
    }
    base.update(orch)
    return {"orchestration": base}


# ───────────────────────── confidence_gate ─────────────────────────
def test_confidence_gate_low_blocks():
    """confidence < 阈 → 否决（不开讲/不报错），降级 = ABORT（维持现场景）。"""
    v = g.confidence_gate(0.59, _cfg(confidence_gate=0.6))
    assert v.allowed is False
    assert v.degradation == Degradation.ABORT
    assert v.reason == "low_confidence"


def test_confidence_gate_at_threshold_passes():
    """confidence == 阈 → 放行（< 才拦，边界含等于，与 PRD §7.4『低于』口径一致）。"""
    v = g.confidence_gate(0.6, _cfg(confidence_gate=0.6))
    assert v.allowed is True
    assert v.degradation is None


def test_confidence_gate_high_passes():
    v = g.confidence_gate(0.95, _cfg(confidence_gate=0.6))
    assert v.allowed is True


def test_confidence_gate_reads_threshold_from_cfg_not_hardcoded():
    """同一 confidence，阈值变 → 裁决翻转 = 证明读 config、未硬编码（契约七）。"""
    c = 0.7
    assert g.confidence_gate(c, _cfg(confidence_gate=0.6)).allowed is True
    assert g.confidence_gate(c, _cfg(confidence_gate=0.8)).allowed is False


def test_verdict_gate_low_confidence_verdict_blocks():
    """verdict=LOW_CONFIDENCE → 否决（门控拦截，改请念该行），即便 confidence 数值够。"""
    r = CheckDraftResult(verdict=Verdict.LOW_CONFIDENCE, confidence=0.99)
    v = g.verdict_gate(r, _cfg())
    assert v.allowed is False
    assert v.degradation == Degradation.ABORT


def test_verdict_gate_unreadable_blocks():
    """verdict=UNREADABLE → 否决（诚实兜底，不编造）。"""
    r = CheckDraftResult(verdict=Verdict.UNREADABLE, confidence=0.99)
    assert g.verdict_gate(r, _cfg()).allowed is False


def test_verdict_gate_found_error_high_conf_passes():
    """found_error + 置信足 → 放行（护栏只守看清/时序，不替 LLM 判语义对错）。"""
    r = CheckDraftResult(verdict=Verdict.FOUND_ERROR, error_line=2, confidence=0.9)
    assert g.verdict_gate(r, _cfg()).allowed is True


def test_verdict_gate_low_numeric_confidence_blocks_before_verdict():
    """confidence 数值低 → 即便 verdict=found_error 也先被置信门挡（联合裁，置信优先）。"""
    r = CheckDraftResult(verdict=Verdict.FOUND_ERROR, error_line=1, confidence=0.3)
    v = g.verdict_gate(r, _cfg(confidence_gate=0.6))
    assert v.allowed is False
    assert v.reason == "low_confidence"


# ───────────────────────── clarify_gate ─────────────────────────
def test_clarify_gate_under_limit_passes():
    v = g.clarify_gate(0, _cfg(clarify_max=1))
    assert v.allowed is True


def test_clarify_gate_at_limit_blocks():
    """已澄清次数 >= clarify_max → 否决再澄清，走 FALLBACK_TEXT。"""
    v = g.clarify_gate(1, _cfg(clarify_max=1))
    assert v.allowed is False
    assert v.degradation == Degradation.FALLBACK_TEXT
    assert v.reason == "clarify_exhausted"


def test_clarify_gate_reads_limit_from_cfg():
    """同 count，阈值放宽 → 翻转放行 = 证明读 config。"""
    assert g.clarify_gate(1, _cfg(clarify_max=1)).allowed is False
    assert g.clarify_gate(1, _cfg(clarify_max=2)).allowed is True


# ───────────────────────── loop_gate（loop 上限）─────────────────────────
def test_loop_gate_under_limit_passes():
    v = g.loop_gate(1, _cfg(max_tool_rounds=2))
    assert v.allowed is True


def test_loop_gate_at_limit_blocks_fallback():
    """已完成轮数 >= max_tool_rounds → 触顶，FALLBACK_TEXT（防失控空转）。"""
    v = g.loop_gate(2, _cfg(max_tool_rounds=2))
    assert v.allowed is False
    assert v.degradation == Degradation.FALLBACK_TEXT
    assert v.reason == "loop_exhausted"


def test_loop_gate_railed_zero_rounds_blocks_immediately():
    """rails 注入 max_tool_rounds=0 → 第 0 轮即触顶（门只忠实读编排传入的阈）。"""
    v = g.loop_gate(0, _cfg(max_tool_rounds=0))
    assert v.allowed is False
    assert v.degradation == Degradation.FALLBACK_TEXT


# ───────────────────────── vision_budget_gate ─────────────────────────
def test_vision_budget_under_passes():
    """识题1 + 批改1 = 2 < 3 → 还能再调一次。"""
    v = g.vision_budget_gate(2, _cfg(vision_budget_per_problem=3))
    assert v.allowed is True


def test_vision_budget_at_limit_blocks():
    """识题1 + 批改2 = 3 → 触顶视觉预算，否决再抓帧，走 ABORT（控成本 C8）。"""
    v = g.vision_budget_gate(3, _cfg(vision_budget_per_problem=3))
    assert v.allowed is False
    assert v.degradation == Degradation.ABORT
    assert v.reason == "vision_budget_exhausted"


def test_vision_budget_reads_from_cfg():
    assert g.vision_budget_gate(3, _cfg(vision_budget_per_problem=3)).allowed is False
    assert g.vision_budget_gate(3, _cfg(vision_budget_per_problem=4)).allowed is True


# ───────────────────────── sticky_fallback（粘滞兜底）─────────────────────────
def test_sticky_fallback_planner_failed_holds_focus():
    """planner 超时/失败 → 否决换场景，维持 current_focus（不漂移），FALLBACK_TEXT。"""
    v = g.sticky_fallback("solve_eq_problem", planner_ok=False, cfg=_cfg())
    assert v.allowed is False
    assert v.degradation == Degradation.FALLBACK_TEXT
    assert v.reason == "planner_unavailable_sticky"
    assert v.detail["sticky_focus"] == "solve_eq_problem"   # focus 原样透传，不漂移


def test_sticky_fallback_planner_ok_passes():
    v = g.sticky_fallback("any_focus", planner_ok=True, cfg=_cfg())
    assert v.allowed is True
    assert v.degradation is None


def test_sticky_fallback_holds_even_when_focus_none():
    """focus 为 None 也维持 None（不无中生有换场景）。"""
    v = g.sticky_fallback(None, planner_ok=False, cfg=_cfg())
    assert v.allowed is False
    assert v.detail["sticky_focus"] is None


# ───────────────────────── 结构性铁律（护栏不可被覆盖 / 在循环外）─────────────────────────
DETERMINISTIC_GATES = [
    g.confidence_gate,
    g.verdict_gate,
    g.clarify_gate,
    g.loop_gate,
    g.vision_budget_gate,
    g.sticky_fallback,
]


@pytest.mark.parametrize("gate", DETERMINISTIC_GATES)
def test_gate_signature_has_no_planner_or_disable_switch(gate):
    """铁律 5：护栏结构上不可被 planner 覆盖、不可被开关关闭。

    → 函数签名里不得出现 planner 实例参数，也不得出现能关掉本护栏的 enable/disable/
       bypass/override/force 开关。sticky_fallback 的 planner_ok 是『planner 是否成功的
       事实布尔』，不是 planner 实例，也不能关闭护栏——单独白名单放行。
    """
    params = set(inspect.signature(gate).parameters)
    forbidden = {
        "planner", "planner_instance", "llm", "model",
        "enabled", "disable", "disabled", "bypass", "override", "force",
        "guard_off", "skip_guard",
    }
    assert params & forbidden == set(), f"{gate.__name__} 暴露了可覆盖/可关闭护栏的参数：{params & forbidden}"


@pytest.mark.parametrize("gate", DETERMINISTIC_GATES)
def test_gate_is_pure_and_deterministic(gate):
    """同输入多次调用结果恒一致（确定性地板，非确定性不进立场与时序，铁律 6）。"""
    cfg = _cfg()
    if gate is g.confidence_gate:
        calls = [(0.59,), (0.6,)]
    elif gate is g.verdict_gate:
        calls = [(CheckDraftResult(verdict=Verdict.UNREADABLE, confidence=0.9),)]
    elif gate in (g.clarify_gate, g.loop_gate, g.vision_budget_gate):
        calls = [(0,), (99,)]
    else:  # sticky_fallback
        calls = None

    if gate is g.sticky_fallback:
        a = gate("f", False, cfg)
        b = gate("f", False, cfg)
        assert a == b
        return

    for args in calls:
        first = gate(*args, cfg)
        for _ in range(3):
            assert gate(*args, cfg) == first


def test_guardrails_does_not_import_orchestrator():
    """护栏在编排循环之外（铁律 5）：模块不依赖 orchestrator / planner 模块。"""
    src = inspect.getsource(g)
    assert "orchestrator" not in src
    assert "import" in src  # sanity：确实有 import（contracts），不是空文件


def test_answer_guard_still_deferred_to_M4():
    """answer_guard 留 M4：仍 raise NotImplementedError（契约九，可选护栏，未到实现期）。"""
    with pytest.raises(NotImplementedError):
        g.answer_guard("候选回复", memory=None, cfg=_cfg())


# ───────────────────────── 与真实 config.yaml 自洽（不硬编码、口径对齐）─────────────────────────
def test_gates_run_against_real_config():
    """护栏能直接吃 load_config() 的真实 config，且行为与 config.yaml 当前阈值一致。

    这条同时证明：护栏读的就是 contracts.config_schema.load_config() 这一权属来源。
    """
    cfg = load_config()
    orch = cfg["orchestration"]
    # confidence_gate：恰好低于真实阈一点 → 必拦；恰好等于 → 必放
    thr = float(orch["confidence_gate"])
    assert g.confidence_gate(thr - 0.01, cfg).allowed is False
    assert g.confidence_gate(thr, cfg).allowed is True
    # loop 上限触顶 = 真实 max_tool_rounds
    assert g.loop_gate(int(orch["max_tool_rounds"]), cfg).allowed is False
    # vision 预算触顶 = 真实 vision_budget_per_problem
    assert g.vision_budget_gate(int(orch["vision_budget_per_problem"]), cfg).allowed is False
    # clarify 触顶 = 真实 clarify_max
    assert g.clarify_gate(int(orch["clarify_max"]), cfg).allowed is False
