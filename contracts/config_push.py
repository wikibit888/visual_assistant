"""契约一辅助 · `config.push` 控制面下发 payload（A → web，走 Channel.control）。

A 在 WS 建连即把 config.yaml 的 `turn_state` / `posture` 两子树原样下发给前端，
让 B/D 在 init 时即拿到非 null 的阈值（前端不自带魔数；契约七 / CLAUDE.md §4）。

单一真源 = config.yaml：本模型**不复述各子键**，只约束「两子树存在且为对象」的结构，
具体键值由 config.yaml 负责（改阈值改 yaml 一处，不在此重复定义 schema）。
config_schema.py 仅有 dict loader、无子模型可复用，故此处新建本 payload 模型。

cfg 接口契约（前端 init 依赖，B/D 须遵守）：
  - cfg.turn_state.*  例：vad_speaking_min_ms / half_duplex_gate / default_voice_mode
  - cfg.posture.*     例：hunchback_hold_ms / reminder_cooldown_ms / thoracic_kyphosis_deg
"""

from pydantic import BaseModel, Field


class ConfigPushPayload(BaseModel):
    """`config.push` 的 payload：config.yaml 的 turn_state + posture 子树快照。"""

    turn_state: dict = Field(
        ..., description="config.turn_state 子树原样下发（vad_speaking_min_ms 等）"
    )
    posture: dict = Field(
        ..., description="config.posture 子树原样下发（hunchback_hold_ms 等）"
    )
