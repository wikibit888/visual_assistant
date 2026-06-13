"""B · 云 ASR 适配器（契约二出口 asr.final）。

`transcribe(audio, turn_id)` → `contracts.AsrFinal`（一个用户回合的最终识别文本）。
- 真实路径：供应商/模型由 `config.roles.asr` 决定，经 `server.llm.providers.client_for_role`
  ("asr", cfg) 取客户端（禁硬编码 provider/model——契约七）。
- `MOCK_ASR=1`（`contracts.mock.is_mock`）：返回固定文本 + confidence，脱依赖、可独立跑（契约六）。
B 只产 AsrFinal、只走信封：本适配器不广播、不旁路，由上层装进 `Envelope(asr.final)` 发总线。
"""

from __future__ import annotations

from typing import Optional

from contracts import AsrFinal
from contracts.mock import is_mock

# MOCK_ASR=1 的固定样本（脱供应商自检用），与 fixtures/voice.jsonl 的 asr.final 对齐。
# 仅桩数据——非阈值、非模型名；真实 confidence 由供应商给出，门控阈值仍由 A 裁决（B 不自决）。
MOCK_TEXT = "这道题我不会，你看看"
MOCK_CONFIDENCE = 0.93


async def transcribe(audio, turn_id: str, cfg: Optional[dict] = None) -> AsrFinal:
    """流式/分段音频 → contracts.AsrFinal（含 confidence + turn_id）。

    `audio`：一个用户回合的原始音频字节（M1-06b 由 /ws 二进制帧透传；容器/编码由前端定，
      真实解码留具体 STT 协议接入时处理）。
    MOCK_ASR=1：忽略 audio 内容、不触供应商、不读 config，直接出固定文本（契约六，可独立运行）。
    真实路径：按 config.roles.asr 经 client_for_role 解析供应商客户端，再调云 STT。
    """
    if is_mock("MOCK_ASR"):
        return AsrFinal(text=MOCK_TEXT, confidence=MOCK_CONFIDENCE, turn_id=turn_id)

    # 真实路径：供应商/模型一律由 config.roles.asr 决定（禁硬编码——契约七）。
    from server.llm.providers import client_for_role

    if cfg is None:
        from contracts.config_schema import load_config

        cfg = load_config()

    # 解析即校验 config 合法（client_for_role 对缺角色/非法 provider 会清晰报错）。
    client_for_role("asr", cfg)
    # 具体云 STT 流式调用：M0 决策 asr=gemini 生态占位，M1 定具体 STT 型号/协议后接入。
    raise NotImplementedError(
        "M1 语音链路：真实云 STT 调用（provider/model 已由 config.roles.asr 经 "
        "client_for_role 解析；待接具体流式 STT 协议）"
    )
