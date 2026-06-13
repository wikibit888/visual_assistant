"""契约八 · Agent 编排循环 + 工具注册表（PRD §7.2 / §7.3 / §7.7，形态 = Pydantic + 文档）。

权属归 A（server/a_core/）。要点（PRD §7.2）：
- planner = deepseek-chat，**温度 0 + 结构化输出（本文件 PlannerOutput）+ 工具白名单**。
- 自由度只留在「措辞（text）」，不留在 `kind` 与工具选择。
- `mode` 隐式切换（选择 A）：sticky，强信号才变，写入工作记忆 current_mode。
- loop 上限 = config orchestration.max_tool_rounds（默认 2）；触顶走 FALLBACK_TEXT。
- planner 软超时 = config roles.planner.planner_timeout_ms（800ms）→ 维持现场景。
- rails（§7.2 / §8）：dispatch 前置可注入 `forced_tool_sequence`（工具序 + answer 节点混合）+
  max_tool_rounds→0；rails 与 agentic **同代码、config 切换**。M0 把序做成可注入 hook。
- E（技能库）不得内嵌路由：路由 = planner 工具选择，无独立意图分类器。

「说话」不是工具，是循环终点；`posture.*` 不进工具表（push，不可被 agent 拉成自由动作）；
无执行类工具（越界 → 「帮不上」）。
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Mode(str, Enum):
    """current_mode（选择 A，sticky）。open=基座；learning/life=对基座的收窄。"""

    OPEN = "open"
    LEARNING = "learning"
    LIFE = "life"


class ToolName(str, Enum):
    """工具白名单（§7.3）。planner 只能从此枚举选工具。"""

    READ_PROBLEM = "read_problem"
    CHECK_DRAFT = "check_draft"
    OBSERVE = "observe"
    WEATHER_GET = "weather_get"
    MEMORY_NOTE = "memory_note"
    MEMORY_RECALL = "memory_recall"


class PlannerKind(str, Enum):
    ANSWER = "answer"          # 快路径：直接出文本
    TOOL_CALLS = "tool_calls"  # 发起 ≤max_tool_rounds 轮工具
    CLARIFY = "clarify"        # 澄清（受 clarify_max 约束）


class ToolCall(BaseModel):
    name: ToolName
    args: dict = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    """planner 结构化输出（JSON schema）。温度 0；自由度只在 text 措辞。"""

    kind: PlannerKind
    mode: Mode
    tools: list[ToolCall] = Field(
        default_factory=list, description="仅 kind=tool_calls 时非空"
    )
    text: Optional[str] = Field(
        None, description="kind=answer/clarify 的措辞；护栏裁决后才出 tts.say"
    )


class ToolSpec(BaseModel):
    """工具注册表条目（§7.3）。in/out_schema 为对应 Pydantic 模型名（文档用）。"""

    name: ToolName
    in_schema: str
    out_schema: str
    mock_env: Optional[str] = None


class RailStep(BaseModel):
    """rails forced_tool_sequence 的一步——工具序 + answer 节点混合（§7.2）。"""

    step: str = Field(..., description='"tool" | "answer"（answer=强制 answer 节点，非工具）')
    name: str


# 工具注册表（§7.3）——纯数据，A 据此校验 planner 工具选择与 dispatch。
TOOL_REGISTRY: dict[str, ToolSpec] = {
    "read_problem": ToolSpec(
        name=ToolName.READ_PROBLEM,
        in_schema="(即刻抓帧，无入参)",
        out_schema="vision.ReadProblemResult",
        mock_env="MOCK_VISION",
    ),
    "check_draft": ToolSpec(
        name=ToolName.CHECK_DRAFT,
        in_schema="(即刻抓帧，无入参)",
        out_schema="vision.CheckDraftResult",
        mock_env="MOCK_VISION",
    ),
    "observe": ToolSpec(
        name=ToolName.OBSERVE,
        in_schema="{hint?: str}",
        out_schema="vision.ObserveResult",
        mock_env="MOCK_VISION",
    ),
    "weather_get": ToolSpec(
        name=ToolName.WEATHER_GET,
        in_schema="weather.WeatherGetArgs",
        out_schema="weather.WeatherResult",
        mock_env="MOCK_WEATHER",
    ),
    "memory_note": ToolSpec(
        name=ToolName.MEMORY_NOTE,
        in_schema="working_memory.MemoryNoteArgs",
        out_schema="(ack)",
        mock_env=None,  # 进程内，恒可独立运行
    ),
    "memory_recall": ToolSpec(
        name=ToolName.MEMORY_RECALL,
        in_schema="working_memory.MemoryRecallArgs",
        out_schema="(value)",
        mock_env=None,
    ),
}
