"""B · 云 TTS 适配器（契约二入口 tts.say / tts.stop）。M0 骨架。MOCK_TTS=1 返回静音/桩。

按句 TTS：首句先播。stop = 立即停 + 清队列 + 回 tts.ack（契约二）。
"""

# from contracts import TtsSay, TtsAck


async def synthesize(say):
    """contracts.TtsSay → 音频帧（按句）。M1 实现。"""
    raise NotImplementedError("M1 语音链路：按句 TTS")
