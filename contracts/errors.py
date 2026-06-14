"""契约十一 · 错误降级（PRD §5 / §8，形态 = 枚举）。

降级动作封闭取值。工具执行体 / 中继据此处置异常并经 `error` 事件建议客户端兜底：
  - RETRY          —— 受 config session.vision_retry_max 约束的有限重试。
  - FALLBACK_TEXT  —— 走字幕兜底（TTS 失败 / Live 断流 → 显示文字 + 文字输入框，PRD §8）。
  - FALLBACK_DATA  —— 用写死数据顶上（天气断网兜底 / 定位失败回落城市，PRD §5）。
  - ABORT          —— 放弃本次动作，维持现场景，不阻塞。
"""

from enum import Enum


class Degradation(str, Enum):
    RETRY = "RETRY"
    FALLBACK_TEXT = "FALLBACK_TEXT"
    FALLBACK_DATA = "FALLBACK_DATA"
    ABORT = "ABORT"
