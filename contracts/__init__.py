"""Visual Assistant v0.1 · 契约层（Live 版，PRD §3 / §4）。

跨进程唯一的「真理来源」。客户端与后端只依赖本包的数据定义通信（客户端 ⇄ 后端单 WS）；
工具执行体与 Live 模型的 function_call 往返亦用本包的 vision/weather/tools schema。

契约清单与落点见 contracts/CONTRACTS.md。
"""

from .envelope import Envelope, SCHEMA_VERSION
from .protocol import MessageType, Channel
from .session import Mode, VoiceMode, SessionStart, SessionUpdate, SessionReady
from .audio import Interrupted
from .transcript import TranscriptRole, Transcript, ToolPhase, ToolActivity
from .frame import FrameRequest, FrameResponse
from .control import TextInput, ErrorEvent
from .posture import PostureAlert
from .vision import (
    VisionKind,
    Verdict,
    ErrorType,
    LookAtPageResult,
    CheckDraftResult,
    ObserveResult,
)
from .weather import WeatherGetArgs, WeatherResult
from .tools import ToolName, ToolSpec, TOOL_REGISTRY, MODE_TOOLSETS
from .config_push import ConfigPushPayload
from .errors import Degradation

__all__ = [
    "Envelope",
    "SCHEMA_VERSION",
    "MessageType",
    "Channel",
    "Mode",
    "VoiceMode",
    "SessionStart",
    "SessionUpdate",
    "SessionReady",
    "Interrupted",
    "TranscriptRole",
    "Transcript",
    "ToolPhase",
    "ToolActivity",
    "FrameRequest",
    "FrameResponse",
    "TextInput",
    "ErrorEvent",
    "PostureAlert",
    "VisionKind",
    "Verdict",
    "ErrorType",
    "LookAtPageResult",
    "CheckDraftResult",
    "ObserveResult",
    "WeatherGetArgs",
    "WeatherResult",
    "ToolName",
    "ToolSpec",
    "TOOL_REGISTRY",
    "MODE_TOOLSETS",
    "ConfigPushPayload",
    "Degradation",
]
