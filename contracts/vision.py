"""契约三 · 视觉结果 schema（PRD §7.7，形态 = 文档 + Pydantic）。

三种 kind：read_problem / check_draft / observe（§7.3 工具注册表）。
批改强制 **四值 verdict**（附录 A）+ error_line/error_type/confidence。
confidence 进 **置信门控**（契约/护栏，config `orchestration.confidence_gate`）：
低于阈值 → 护栏不播报错误、改请用户口述（PRD §7.4）。
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class VisionKind(str, Enum):
    READ_PROBLEM = "read_problem"
    CHECK_DRAFT = "check_draft"
    OBSERVE = "observe"


class Verdict(str, Enum):
    """四值 verdict（附录 A）—— 批改结果的封闭取值。"""

    FOUND_ERROR = "found_error"        # 定位到错误（须给 error_line）
    ALL_CORRECT = "all_correct"        # 全对
    UNREADABLE = "unreadable"          # 看不清/无法识别（诚实兜底，不编造）
    LOW_CONFIDENCE = "low_confidence"  # 识别了但置信不足（门控拦截，改请念该行）


class ErrorType(str, Enum):
    """error_type 占位枚举；具体细化由 C/E 推进，不属 M0 红线。"""

    SIGN_ERROR = "sign_error"
    TRANSPOSE_ERROR = "transpose_error"  # 移项符号错误（演示预埋）
    CALC_ERROR = "calc_error"
    OTHER = "other"


class ReadProblemResult(BaseModel):
    """payload of vision.result（kind=read_problem）。识题成功后缓存 active_problem。"""

    kind: VisionKind = VisionKind.READ_PROBLEM
    problem_text: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class CheckDraftResult(BaseModel):
    """payload of vision.result（kind=check_draft）。只定位错误行与类型，不报正确答案。"""

    kind: VisionKind = VisionKind.CHECK_DRAFT
    verdict: Verdict
    error_line: Optional[int] = Field(
        None, ge=1, description="1-based 草稿行号；found_error 必填"
    )
    error_type: Optional[ErrorType] = None
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _found_error_needs_line(self):
        if self.verdict == Verdict.FOUND_ERROR and self.error_line is None:
            raise ValueError("verdict=found_error 必须给出 error_line（契约三）")
        return self


class ObserveResult(BaseModel):
    """payload of vision.result（kind=observe）。穿搭/即兴物体通用。"""

    kind: VisionKind = VisionKind.OBSERVE
    description: str
    confidence: float = Field(..., ge=0.0, le=1.0)
