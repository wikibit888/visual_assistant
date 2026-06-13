"""A · 轮次状态机 + 间隙仲裁（契约四；PRD §7.7 / §3.2.2）。M0 骨架。

维护五态（TurnState）；**A 是 gap.open 的唯一广播者**。
间隙规则（数值全取 config.turn_state，禁硬编码）：
  - IDLE ≥ idle_to_gap_ms → 广播 gap.open（窗口 gap_window_ms）
  - 对讲机间隙 = 松键 + TTS 结束 ≥ ptt_gap_after_tts_ms
  - 持续 no_gap_force_insert_ms 无间隙 → 允许句界插入
姿态放行门控（§3.2.2，根因级）：posture.alert 仅在
  current_mode==learning 或 active_problem!=null 时放行；可丢弃不排队；60s 冷却。
"""

# from contracts import GapOpen, TurnState, PostureAlert


def should_release_posture(memory):
    """放行门控：current_mode==learning 或 active_problem!=null。M2 实现。"""
    raise NotImplementedError("M2：坐姿放行并入 active_problem，防 mode 抖动吞 P0")


def open_gap(turn_id, cfg):
    """构造并广播 gap.open。M1/M2 实现。"""
    raise NotImplementedError("M2")
