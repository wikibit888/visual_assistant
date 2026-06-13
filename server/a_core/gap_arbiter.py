"""A · 轮次状态机 + 间隙仲裁（契约四；PRD §7.7 / §3.2.2 / §7.5）。

维护五态（contracts.TurnState）；**A 是 gap.open 的唯一广播者**（CLAUDE.md 铁律 2）。
本文件只负责「立场与时序」的确定性裁决（PRD §1.3）——数值全取 config.turn_state，禁硬编码。

间隙规则（数值全取 config.turn_state）：
  - IDLE 持续 ≥ idle_to_gap_ms → 构造 gap.open（窗口 gap_window_ms）。
  - 对讲机间隙 = 松键 + TTS 结束 ≥ ptt_gap_after_tts_ms（确定性）。
  - 持续 no_gap_force_insert_ms 无间隙 → 允许句界插入（坐姿提醒兜底）。

姿态放行门控（PRD §3.2.2，根因级解耦）：
  posture.alert 仅在 current_mode==learning 或 active_problem!=null 时放行。
  ⚠ 放行条件并入 active_problem!=null，把放行从「易被 planner 隐式抖动」的
  current_mode flag 解耦到更稳的 active_problem 状态——防 mode 误判把 P0 坐姿提醒静默吞掉。
  （只读这两个 flag、不读 planner；提醒话术由护栏层在 gap 选模板，D 不带文本。）
"""

from contracts import GapOpen, Mode


def should_release_posture(memory) -> bool:
    """姿态放行门控（PRD §3.2.2 / §7.7 契约四）。

    放行 iff current_mode==learning **或** active_problem!=null。
    两条件取并集（OR）：放行解耦到 active_problem，防 mode 抖动吞掉 P0 坐姿提醒。
    只读工作记忆的这两个 flag，不读 planner（红线不破）。
    """
    in_learning = getattr(memory, "current_mode", None) == Mode.LEARNING
    has_active_problem = getattr(memory, "active_problem", None) is not None
    return in_learning or has_active_problem


def should_open_gap(idle_elapsed_ms: int, cfg: dict) -> bool:
    """确定性判定：IDLE 已持续 ≥ config.turn_state.idle_to_gap_ms 即可开窗。

    数值取 config.turn_state.idle_to_gap_ms（默认 2000ms），禁硬编码。
    构造 gap.open 由 open_gap 负责（gap.open 唯一广播点，铁律 2）。
    """
    idle_to_gap_ms = cfg["turn_state"]["idle_to_gap_ms"]
    return idle_elapsed_ms >= idle_to_gap_ms


def open_gap(turn_id: str, cfg: dict) -> GapOpen:
    """构造 gap.open 的 payload —— **A 唯一的 gap.open 构造点**（CLAUDE.md 铁律 2）。

    window_ms 取自 config.turn_state.gap_window_ms（默认 1000ms），禁硬编码。
    护栏层据返回的 GapOpen 择机在窗口内插入坐姿提醒（可丢弃不排队）。
    调用方负责判定 IDLE 时序（见 should_open_gap）后再调本函数构造。
    """
    window_ms = cfg["turn_state"]["gap_window_ms"]
    return GapOpen(turn_id=turn_id, window_ms=window_ms)
