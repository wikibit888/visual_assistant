"""契约四 · 字幕 + 工具活动（PRD §4.1，形态 = Pydantic）。后端 → 客户端单向。

两类「让用户看见正在发生什么」的事件：
  - `transcript`：Live 模型给出的用户/助手转写文本，用于前端字幕 / 历史记录。
    仅在 `session.subtitles=true` 时下发。`final=false` = 流式增量（可被后续覆盖）。
  - `tool.activity`：工具执行体在动的提示（如「看了一眼纸面」），供前端给轻量 UI 反馈；
    **同时是客户端置 `active_problem` 的依据**——learning 模式下收到 look_at_page 完成事件即置位
    （客户端确定性状态，PRD §3.2.2 坐姿放行门控读它）。客户端不解析工具内部结果，只读名字与阶段。
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TranscriptRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class Transcript(BaseModel):
    """payload of `transcript` —— 一段字幕（用户或助手）。"""

    role: TranscriptRole
    text: str
    final: bool = Field(True, description="false=流式增量，可被后续同角色片段覆盖")


class ToolPhase(str, Enum):
    START = "start"  # 工具开始执行（UI 可显示「看一眼…」）
    DONE = "done"    # 工具完成（客户端据此在 learning 置 active_problem）


class ToolActivity(BaseModel):
    """payload of `tool.activity` —— 工具执行体在动。客户端只读 name/phase，不读内部结果。"""

    name: str = Field(..., description="工具名（contracts.tools.ToolName 之一）")
    phase: ToolPhase
    summary: Optional[str] = Field(
        None, description="可选的一句话 UI 提示（如「看了一眼草稿」）；不含敏感识别内容"
    )
