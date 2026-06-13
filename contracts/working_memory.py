"""契约十 · 工作记忆 schema（PRD §7.7，形态 = Pydantic）。**仅内存、不落盘。**

字段严格对齐 PRD §7.7 契约十枚举。current_mode sticky（强信号才变）。
solved_answer 为独立字段（PRD 列举），不播报、不入明文日志（配合答案护栏契约九）。
memory.note / memory.recall（§7.3）= 对本结构的进程内读写工具。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from .orchestration import Mode


class ActiveProblem(BaseModel):
    """识题成功后缓存；后续走零工具快路径（PRD §3.1）。"""

    problem_text: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class MistakeEntry(BaseModel):
    error_line: Optional[int] = None
    error_type: Optional[str] = None
    ts: int


class WorkingMemory(BaseModel):
    current_mode: Mode = Field(Mode.OPEN, description="sticky，强信号才变；门控工具子集与坐姿放行")
    active_problem: Optional[ActiveProblem] = None
    solved_answer: Optional[str] = Field(None, description="不播报、不入明文日志")
    current_focus: Optional[str] = None
    mistake_log: list[MistakeEntry] = Field(default_factory=list)
    reminder_count: int = 0   # 坐姿提醒次数，计入口头小结
    clarify_count: int = 0    # 每 focus 澄清次数，受 clarify_max(1) 约束


class MemoryNoteArgs(BaseModel):
    """memory.note 入参（§7.3）。"""

    key: str
    value: Any = None


class MemoryRecallArgs(BaseModel):
    """memory.recall 入参（§7.3）。"""

    key: str
