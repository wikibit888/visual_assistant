"""契约十三 · `config.push` 控制面下发 payload（后端 → 客户端，建连即发）。

后端在 WS 建连即把 config.yaml 的 `posture` / `voice` 两子树原样下发给前端，让端侧 D（姿态）与
B（语音/客户端闸门）在 init 时即拿到非 null 阈值——**前端不自带魔数**（阈值单一真源 = config.yaml）。

单一真源 = config.yaml：本模型**不复述各子键**，只约束「子树存在且为对象」；具体键值由
config.yaml 负责（改阈值改 yaml 一处）。前端 init 依赖的 cfg 接口：
  - cfg.posture.*  例：hunchback_hold_ms / reminder_cooldown_ms / thoracic_kyphosis_deg /
                       head_forward_ratio / release_scope / gap_min_silence_ms
  - cfg.voice.*    例：default_voice_mode / half_duplex_gate / barge_in_min_ms
  - cfg.audio.*    in_sample_rate(16000 上行) / out_sample_rate(24000 下行)——前端采播 PCM 用，
                   取自 config.session.audio_in/out_sample_rate（前端不硬编码采样率魔数）
"""

from pydantic import BaseModel, Field


class ConfigPushPayload(BaseModel):
    """`config.push` 的 payload：config.yaml 的 posture + voice + audio 快照（前端阈值/采样率）。"""

    posture: dict = Field(..., description="config.posture 子树原样下发（端侧 D 用）")
    voice: dict = Field(..., description="config.voice 子树原样下发（端侧 B / 客户端闸门用）")
    audio: dict = Field(
        ..., description="音频采样率 {in_sample_rate, out_sample_rate}（前端采播用，取自 config.session）"
    )
