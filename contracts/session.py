"""契约二 · 会话生命周期 + 模式（PRD §2 / §4，形态 = Pydantic）。

新架构核心简化：**`mode` 不再由模型隐式推断，而是用户在前端右上角显式选**（学习/生活/开放，
PRD §2 三支柱）。`voice_mode` 是每个模式内部的语音切换（对讲机 PTT / 自由对话 VAD，PRD §4.3/§4.4）。
开放对话 = 基座，学习/生活 = 对基座叠约束 profile 的收窄（PRD §2）——同一套 Live 会话，换系统提示
profile + 工具子集，不是三条流水线。

`mode` 决定后端给 Live 会话注入哪个系统提示 profile（server/skills）与工具可用子集；
客户端亦据 `mode==learning` 决定是否显示坐姿指示器、是否放行坐姿提醒（PRD §3.2.2）。
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Mode(str, Enum):
    """三支柱（PRD §2）。open=基座；learning/life=对基座的收窄 profile。前端右上角显式选。"""

    OPEN = "open"
    LEARNING = "learning"
    LIFE = "life"


class VoiceMode(str, Enum):
    """语音轮次模式（PRD §4.3/§4.4）。每个 mode 内部可切；开场默认 ptt（确定性、防自激）。"""

    PTT = "ptt"    # 对讲机：按住说话，松手即轮次结束信号（默认）
    FREE = "free"  # 自由对话：免按，模型原生 VAD 判轮次 + barge-in（高光）


class SessionStart(BaseModel):
    """payload of `session.start` —— 客户端请求后端开一个 Live 会话。"""

    mode: Mode = Mode.OPEN
    voice_mode: VoiceMode = VoiceMode.PTT
    subtitles: bool = Field(True, description="是否下发 transcript 给前端显示字幕")
    lat: Optional[float] = Field(
        None,
        description="客户端 navigator.geolocation 纬度（生活模式天气定位）；缺省/拒绝 → 后端回落默认城市（PRD §5）",
    )
    lon: Optional[float] = Field(None, description="经度；同 lat，成对注入 weather_get，不让模型现编坐标")


class SessionUpdate(BaseModel):
    """payload of `session.update` —— 运行时切换（任一字段为 None 表示不变）。

    切 `mode` = 后端换 Live 会话的系统提示 profile + 工具子集；切 `voice_mode` = 改轮次策略
    （PTT↔VAD）；切 `subtitles` = 开/关字幕下发。前端右上角换模式 / 模式内切语音 / 字幕开关均经此。
    """

    mode: Optional[Mode] = None
    voice_mode: Optional[VoiceMode] = None
    subtitles: Optional[bool] = None


class SessionReady(BaseModel):
    """payload of `session.ready` —— 后端确认 Live 会话已建立，回声当前生效配置。"""

    session_id: str
    mode: Mode
    voice_mode: VoiceMode
