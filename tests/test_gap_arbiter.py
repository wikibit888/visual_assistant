"""M1-04 验收：A 状态机 + 间隙仲裁（契约四；PRD §3.2.2 / §7.5 / §7.7）。

零外部服务 / 零 LLM：纯确定性「立场与时序」裁决，数值取 config.turn_state（禁硬编码）。
覆盖：
  - IDLE 持续 ≥ idle_to_gap_ms → 开窗判定为真；< 阈值不开窗（边界含等号）。
  - open_gap 构造合规 GapOpen，window_ms == config.turn_state.gap_window_ms（默认 1s）。
  - gap.open 唯一构造点在 A（铁律 2）：open_gap 返回 contracts.GapOpen。
  - 姿态放行门控两条件各自独立放行（learning ∨ active_problem），且都为假时不放行。
"""

from contracts import ActiveProblem, GapOpen, Mode, WorkingMemory
from contracts.config_schema import load_config
from server.a_core.gap_arbiter import (
    open_gap,
    should_open_gap,
    should_release_posture,
)

# 真实 config（契约值唯一来源，禁在测试里硬编码阈值）。
CFG = load_config()
IDLE_TO_GAP_MS = CFG["turn_state"]["idle_to_gap_ms"]
GAP_WINDOW_MS = CFG["turn_state"]["gap_window_ms"]


# ── IDLE → gap.open 时序门控（数值取 config，禁硬编码）────────────────────────
def test_idle_below_threshold_does_not_open():
    """IDLE 持续 < idle_to_gap_ms → 不开窗。"""
    assert should_open_gap(IDLE_TO_GAP_MS - 1, CFG) is False


def test_idle_at_threshold_opens():
    """边界含等号：IDLE 持续 == idle_to_gap_ms → 开窗（PRD §7.7：≥2s）。"""
    assert should_open_gap(IDLE_TO_GAP_MS, CFG) is True


def test_idle_above_threshold_opens():
    """IDLE 持续 > idle_to_gap_ms → 开窗。"""
    assert should_open_gap(IDLE_TO_GAP_MS + 5000, CFG) is True


def test_should_open_gap_reads_config_not_hardcoded():
    """门控阈值来自 config：临时拔高阈值，原本够开窗的时长也不再开窗。"""
    raised = {"turn_state": {**CFG["turn_state"], "idle_to_gap_ms": IDLE_TO_GAP_MS + 10_000}}
    assert should_open_gap(IDLE_TO_GAP_MS, raised) is False


# ── open_gap = gap.open 唯一构造点（铁律 2），window_ms 取 config ─────────────
def test_open_gap_returns_contract_gapopen():
    """open_gap 产出合规 contracts.GapOpen（gap.open 唯一构造点，铁律 2）。"""
    out = open_gap("t-000123", CFG)
    assert isinstance(out, GapOpen)
    GapOpen.model_validate(out.model_dump())  # 构造 → 序列化 → 再校验


def test_open_gap_window_equals_config_gap_window_ms():
    """window_ms == config.turn_state.gap_window_ms（默认 2s→窗口 1s，视 config 实际值）。"""
    out = open_gap("t-000123", CFG)
    assert out.window_ms == GAP_WINDOW_MS


def test_open_gap_carries_turn_id():
    """A 在间隙仲裁时按当时回合关联 turn_id（契约四 GapOpen.turn_id）。"""
    out = open_gap("t-000777", CFG)
    assert out.turn_id == "t-000777"


def test_open_gap_window_reads_config_not_hardcoded():
    """window_ms 来自 config：改 config 的 gap_window_ms，输出随之变（非硬编码 1000）。"""
    bumped = {"turn_state": {**CFG["turn_state"], "gap_window_ms": GAP_WINDOW_MS + 500}}
    out = open_gap("t-000123", bumped)
    assert out.window_ms == GAP_WINDOW_MS + 500


# ── 姿态放行门控：learning ∨ active_problem（PRD §3.2.2 根因级解耦）──────────
def test_release_when_mode_learning():
    """条件一：current_mode==learning（无 active_problem 也放行）。"""
    mem = WorkingMemory(current_mode=Mode.LEARNING, active_problem=None)
    assert should_release_posture(mem) is True


def test_release_when_active_problem_set():
    """条件二（根因级解耦）：active_problem!=null → 放行，即使 mode 抖回非 learning。

    防 mode 误判把 P0 坐姿提醒静默吞掉（PRD §3.2.2 ⚠ 关键修法）。
    """
    mem = WorkingMemory(
        current_mode=Mode.OPEN,
        active_problem=ActiveProblem(problem_text="2x+3=7", confidence=0.9),
    )
    assert should_release_posture(mem) is True


def test_release_when_both_conditions_set():
    """两条件同时为真 → 放行。"""
    mem = WorkingMemory(
        current_mode=Mode.LEARNING,
        active_problem=ActiveProblem(problem_text="2x+3=7", confidence=0.9),
    )
    assert should_release_posture(mem) is True


def test_no_release_when_neither_condition():
    """两条件皆假（open 且无 active_problem）→ 不放行：坐姿提醒可丢弃不排队。"""
    mem = WorkingMemory(current_mode=Mode.OPEN, active_problem=None)
    assert should_release_posture(mem) is False


def test_no_release_in_life_mode_without_active_problem():
    """life 模式且无 active_problem → 不放行（只学习语境放行）。"""
    mem = WorkingMemory(current_mode=Mode.LIFE, active_problem=None)
    assert should_release_posture(mem) is False
