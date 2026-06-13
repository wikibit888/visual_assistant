"""B · 云 TTS 适配器（契约二入口 tts.say / tts.stop）。

权威语义（PRD §7.5 / 契约二 voice.py）：
- 按句 TTS：synthesize(say) 把一句 `tts.say` 文本**按句切分**，逐帧产出音频；
  `seq` 自增，**首句 seq 最小、先播**（盖往返延迟、降首响）。
- `tts.stop` = **立即停 + 清播放队列 + 回 `contracts.TtsAck`**（打断必须可感知确认）。
  半双工 gate / 真正的扬声器播放在前端 b_voice.js；后端只产帧 + 维护可被 stop 清空的队列。
- 真实路径：供应商/模型由 `config.roles.tts` 决定，经 `server.llm.providers.client_for_role`
  ("tts", cfg) 取客户端（禁硬编码 provider/model——契约七）。providers 只读不改。
- `MOCK_TTS=1`（`contracts.mock.is_mock`）：产静音桩帧，脱依赖、可独立跑（契约六）。

铁律 2：`tts.*` 只发 B；本适配器不广播、不旁路，由上层装进 `Envelope` 走总线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional

from contracts import TtsAck, TtsSay
from contracts.mock import is_mock

# MOCK_TTS=1 的静音桩：每句一帧静音 PCM（非阈值、非模型名——纯桩数据）。
# 真实采样率/编码由供应商决定；MOCK 只需可被切分/计数的占位字节。
MOCK_SILENCE_FRAME = b"\x00\x00"

# 句界切分：中文标点（。！？；…）与西文 . ! ? ; 均作句界，保留标点随前句。
_SENTENCE_SPLIT = re.compile(r"[^。！？；…!?;.]*[。！？；…!?;.]|[^。！？；…!?;.]+")


def split_sentences(text: str) -> List[str]:
    """按句切分：保留句末标点，去空白；空串 → []。

    确定性纯函数（无供应商、无 IO）：首句即返回列表第 0 项，seq 据此自增。
    """
    if not text or not text.strip():
        return []
    parts = [m.group(0).strip() for m in _SENTENCE_SPLIT.finditer(text)]
    return [p for p in parts if p]


@dataclass
class TtsFrame:
    """synthesize 产出的单帧音频。

    seq 自增、首句 seq 最小先播；text 为该句原文（便于上层日志/对齐）；
    audio 为该句音频字节（MOCK 下为静音桩）。
    """

    turn_id: str
    seq: int
    text: str
    audio: bytes


@dataclass
class TtsAdapter:
    """有状态 TTS 适配器：维护可被 stop 清空的待播队列。

    synthesize() 按句产帧并入队（首句先播）；stop() 立即停 + 清队列 + 回 TtsAck。
    queue 仅记录「尚未交付前端播放」的句子，stop 据此清空、可被上层观测。
    """

    cfg: Optional[dict] = None
    _queue: List[TtsFrame] = field(default_factory=list)
    _stopped: bool = False

    async def synthesize(self, say: TtsSay) -> AsyncIterator[TtsFrame]:
        """contracts.TtsSay → 逐句音频帧（异步生成器，首句 seq 最小先播）。

        每句产一帧：seq 自基准 say.seq 自增，turn_id 透传。
        MOCK_TTS=1：产静音桩、不触供应商（契约六）。
        真实路径：经 client_for_role("tts", cfg) 解析供应商（契约七），再调云 TTS。
        """
        self._stopped = False
        sentences = split_sentences(say.text)
        if not sentences:
            return

        if is_mock("MOCK_TTS"):
            synth = self._mock_synth_sentence
        else:
            synth = await self._real_synth_factory()

        base = say.seq
        for offset, sentence in enumerate(sentences):
            if self._stopped:
                break
            frame = TtsFrame(
                turn_id=say.turn_id,
                seq=base + offset,
                text=sentence,
                audio=await synth(sentence),
            )
            self._queue.append(frame)
            yield frame

    def stop(self, turn_id: str) -> TtsAck:
        """立即停 + 清播放队列 + 回 contracts.TtsAck（契约二 stop 语义）。

        置 _stopped 旗标令进行中的 synthesize 在下一句界处停产；清空待播队列。
        返回 stopped=True 的 ack——打断必须可感知确认（PRD §7.5 / §5.2 X-1）。
        """
        self._stopped = True
        self._queue.clear()
        return TtsAck(turn_id=turn_id, stopped=True)

    @property
    def pending(self) -> List[TtsFrame]:
        """尚未播放（未被 stop 清空）的队列快照——供上层/测试观测。"""
        return list(self._queue)

    async def _mock_synth_sentence(self, sentence: str) -> bytes:
        """MOCK 桩：每句一帧静音占位（不触供应商，契约六）。"""
        return MOCK_SILENCE_FRAME

    async def _real_synth_factory(self):
        """真实路径：按 config.roles.tts 经 client_for_role 解析供应商客户端。

        cfg 缺省时走 load_config()；解析即校验 config 合法（缺角色/非法 provider
        由 client_for_role 清晰报错——契约七，禁硬编码 provider/model）。
        具体云 TTS 流式合成（gemini 生态占位，M1 定具体 TTS 协议）后接入。
        """
        from server.llm.providers import client_for_role

        cfg = self.cfg
        if cfg is None:
            from contracts.config_schema import load_config

            cfg = load_config()

        client_for_role("tts", cfg)  # 解析 + 校验（provider/model 取自 config.roles.tts）
        raise NotImplementedError(
            "M1 语音链路：真实云 TTS 合成（provider/model 已由 config.roles.tts 经 "
            "client_for_role 解析；待接具体流式 TTS 协议）"
        )


async def synthesize(say: TtsSay, cfg: Optional[dict] = None) -> AsyncIterator[TtsFrame]:
    """无状态便捷入口：单句 tts.say → 逐句音频帧（首句先播）。

    需要 stop 语义/队列观测时用 TtsAdapter（持有可被 stop 清空的队列）。
    """
    adapter = TtsAdapter(cfg=cfg)
    async for frame in adapter.synthesize(say):
        yield frame
