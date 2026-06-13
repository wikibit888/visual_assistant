"""Visual Assistant v0.1 · 契约层（M0 钉死，PRD §7.7）。

这是跨模块唯一的「真理来源」。所有模块只依赖本包的数据定义，
禁止互相 import 对方的内部对象（PRD §7.1 铁律）。

契约清单与落点见 contracts/CONTRACTS.md。
"""

from .envelope import Envelope, SCHEMA_VERSION
from .message_types import MessageType, Channel
from .voice import AsrFinal, TtsSay, TtsStop, TtsAck
from .vision import (
    VisionKind,
    Verdict,
    ErrorType,
    ReadProblemResult,
    CheckDraftResult,
    ObserveResult,
)
from .state_machine import TurnState, PostureAlert, GapOpen
from .errors import Degradation
from .weather import WeatherGetArgs, WeatherResult
from .orchestration import (
    Mode,
    ToolName,
    PlannerKind,
    PlannerOutput,
    ToolCall,
    ToolSpec,
    RailStep,
    TOOL_REGISTRY,
)
from .answer_guard import AnswerGuardConfig, GuardDecision
from .working_memory import (
    WorkingMemory,
    ActiveProblem,
    MistakeEntry,
    MemoryNoteArgs,
    MemoryRecallArgs,
)

__all__ = [
    "Envelope",
    "SCHEMA_VERSION",
    "MessageType",
    "Channel",
    "AsrFinal",
    "TtsSay",
    "TtsStop",
    "TtsAck",
    "VisionKind",
    "Verdict",
    "ErrorType",
    "ReadProblemResult",
    "CheckDraftResult",
    "ObserveResult",
    "TurnState",
    "PostureAlert",
    "GapOpen",
    "Degradation",
    "WeatherGetArgs",
    "WeatherResult",
    "Mode",
    "ToolName",
    "PlannerKind",
    "PlannerOutput",
    "ToolCall",
    "ToolSpec",
    "RailStep",
    "TOOL_REGISTRY",
    "AnswerGuardConfig",
    "GuardDecision",
    "WorkingMemory",
    "ActiveProblem",
    "MistakeEntry",
    "MemoryNoteArgs",
    "MemoryRecallArgs",
]
