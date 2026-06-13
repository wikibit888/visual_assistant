"""B · 云 ASR 适配器（契约二出口 asr.final）。M0 骨架。MOCK_ASR=1 返回固定文本。"""

# from contracts import AsrFinal


async def transcribe(audio, turn_id):
    """流式/分段 ASR → contracts.AsrFinal。M1 实现。"""
    raise NotImplementedError("M1 语音链路：流式 ASR")
