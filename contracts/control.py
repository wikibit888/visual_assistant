"""契约六 · 控制面杂项（文字输入兜底 + 错误/降级，PRD §5 / §8，形态 = Pydantic）。

  - `text.input`：语音链路降级时的文字输入兜底（PRD §5：断网/TTS 失败 → 字幕 + 文字键入）。
    客户端把用户键入文本上行，后端注入 Live 会话当作一个用户轮次。
  - `error`：后端向客户端报错/报降级（如 weather 失败回落、视觉低置信、Live 断流），
    客户端据 `degradation` 决定 UI 兜底（字幕提示 / 切对讲机 / 显示文字输入框）。
"""

from typing import Optional

from pydantic import BaseModel, Field

from .errors import Degradation


class TextInput(BaseModel):
    """payload of `text.input` —— 文字输入兜底（语音降级时）。"""

    text: str


class ErrorEvent(BaseModel):
    """payload of `error` —— 后端 → 客户端的错误/降级提示。"""

    code: str = Field(..., description="机器可读错误码，如 live_disconnected / vision_low_confidence")
    message: str = Field(..., description="可向用户展示的简短说明")
    degradation: Optional[Degradation] = Field(
        None, description="建议的降级动作（契约·错误降级）；None=仅告知不降级"
    )
