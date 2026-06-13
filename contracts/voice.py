"""契约二 · ASR 出口 / TTS 入口（PRD §7.7，形态 = 文档 + Pydantic payload）。

权威语义（PRD §7.5 / §5.2 X-1）：
- `tts.stop` = **立即停 + 清播放队列 + 回 `tts.ack`**（打断必须可感知地确认）。
- AI_SPEAKING 期间 B 端 **半双工 gate 麦克风**（暂停采音，消灭自激）；详见 state_machine.py。
- 按句 TTS：首句先播（seq 递增）；工具回合用复述/填充语盖往返延迟。
这些是 B 模块（web/src/modules/b_voice.js + server/b_voice/）的实现义务，A 只发 `tts.say/stop`。
"""

from pydantic import BaseModel, Field


class AsrFinal(BaseModel):
    """payload of `asr.final` —— 一个用户回合的最终识别文本。"""

    text: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    turn_id: str


class TtsSay(BaseModel):
    """payload of `tts.say` —— A 让 B 播一句（已过确定性护栏）。"""

    text: str
    turn_id: str
    seq: int = Field(0, description="同回合按句序号，首句先播")


class TtsStop(BaseModel):
    """payload of `tts.stop` —— 立即停 + 清队列 + ack。"""

    turn_id: str


class TtsAck(BaseModel):
    """payload of `tts.ack` —— B 对 stop 的确认。"""

    turn_id: str
    stopped: bool = True
