"""契约四 · 轮次/IO 状态机 + 间隙仲裁（PRD §7.7，形态 = 文档）。

五态：
  USER_SPEAKING → PLANNING_ACTING → AI_SPEAKING → IDLE →（≥idle_to_gap_ms）→ GAP
转移与窗口（数值全在 config.yaml `turn_state`，禁硬编码）：
  - IDLE ≥ idle_to_gap_ms(2s) → **A 唯一广播 `gap.open`**（窗口 gap_window_ms=1s）。
  - 对讲机间隙判定 = 松键 + TTS 结束 ≥ ptt_gap_after_tts_ms(1s)，确定性。
  - 持续 no_gap_force_insert_ms(20s) 无间隙 → 允许句界插入（坐姿提醒兜底）。
  - **AI_SPEAKING 期间半双工 gate 麦克风**（half_duplex_gate=true）：暂停采音消灭自激。

姿态放行门控（§3.2.2，根因级解耦）：
  `posture.alert` 仅在 `current_mode==learning` 或 `active_problem!=null` 时放行；
  只在 `gap.open` 窗口播出；可丢弃不排队；同类 reminder_cooldown_ms(60s) 冷却。
  D 模块 100% 端侧，只发 `posture.alert`，绝不出声、绝不入 agent loop。
"""

from enum import Enum

from pydantic import BaseModel, Field


class TurnState(str, Enum):
    USER_SPEAKING = "user_speaking"
    PLANNING_ACTING = "planning_acting"
    AI_SPEAKING = "ai_speaking"
    IDLE = "idle"
    GAP = "gap"


class PostureAlert(BaseModel):
    """payload of `posture.alert` —— D（端侧）唯一出口。话术由护栏层在 gap 选模板，D 不带文本。

    turn_id 说明：D 端侧无用户回合上下文，发信封时 turn_id 用当前值或哨兵；
    **A 在接收/间隙仲裁时按当时回合关联 turn_id**（posture.alert 与用户回合非强绑定）。
    """

    severity: str = Field("hunchback", description="v0.1 单级；不升级、不带话术")
    ts: int


class GapOpen(BaseModel):
    """payload of `gap.open` —— A 唯一广播；护栏层据此择机插入坐姿提醒。"""

    turn_id: str
    window_ms: int = Field(..., description="开窗时长，取自 config turn_state.gap_window_ms")
