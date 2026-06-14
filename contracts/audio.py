"""契约三 · 音频轮次控制 + 打断（PRD §4.3 / §4.4 / §5，形态 = 文档 + Pydantic）。

**音频本体不在本契约面**：PCM 帧走 WS 二进制帧（上行 16k、下行 24k，采样率在 config.session），
裸字节、不裹信封——这是客户端 B 与后端中继的「内部传输」（高频，省 JSON 开销）。

本文件只定义与音频相关的**控制信封 payload**：
  - 对讲机 PTT 轮次边界（`input.activity_start/end`）——按钮物理状态明确界定轮次，不靠模型猜
    （PRD §4.3）；后端据此向 Live 会话发 activityStart/activityEnd。自由对话模式不发这两条，
    由模型原生 VAD 判轮次（PRD §4.4）。
  - `interrupted`——barge-in：Live 模型检测到用户在其说话时开口 → 后端下发，客户端立即停播 + 清
    播放队列（PRD §4.4 / §5 处理中被打断）。

`input.activity_start/end` payload 为空对象 `{}`（边界本身即信号）；故本文件只给一个 `Interrupted`。
"""

from typing import Optional

from pydantic import BaseModel, Field


class Interrupted(BaseModel):
    """payload of `interrupted` —— 后端通知客户端：停播 + 清播放队列（barge-in / 新轮次取代）。"""

    reason: Optional[str] = Field(
        None, description="barge_in（用户打断）| superseded（新轮次取代）| stop（显式停）"
    )
