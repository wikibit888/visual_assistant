"""契约八 · 视觉工具结果 schema（PRD §3 工具注册表 / §5，形态 = 文档 + Pydantic）。

三个视觉工具（function_call → 后端工具执行体）的返回。**不经客户端 WS**：是后端 `function_response`
回给 Live 模型的结构化结果（客户端只通过 tool.activity 感知工具在动）。

确定性落点（PRD §1 / §4.1 绿层）：工具执行体抓帧 + 调视觉识别（gemini-2.5-flash）→ 返回带
`confidence` 的结构化结果，**绝不编造**。低置信如何处理 = **提示词约束**（系统提示告诉模型
「confidence 低就请用户挪近 / 口述，别硬读」，PRD §5），不再有出站文本护栏。视觉次数由工具执行层
按 `config.session.vision_budget_per_problem` 计数封顶（PRD §5 抓帧超预算）。

  - look_at_page → {text, confidence}      看一眼纸面：识题 or 读草稿原文（不解题）
  - check_draft  → {verdict, error_line?, confidence}  批改：只定位错误「行+类型」，不报正确答案
  - observe      → {description, confidence} 看画面里的东西（穿搭/随手物体），一句客观描述
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class VisionKind(str, Enum):
    LOOK_AT_PAGE = "look_at_page"
    CHECK_DRAFT = "check_draft"
    OBSERVE = "observe"


class Verdict(str, Enum):
    """批改判决（封闭取值）。低置信不再是 verdict——`confidence` 是独立字段，由提示词约束处置。"""

    FOUND_ERROR = "found_error"   # 定位到错误（必须给 error_line）
    ALL_CORRECT = "all_correct"   # 全对
    UNREADABLE = "unreadable"     # 看不清/无法识别（诚实兜底，不编造）


class ErrorType(str, Enum):
    """error_type 占位枚举；具体细化由工具执行体/提示词推进，非红线。"""

    SIGN_ERROR = "sign_error"
    TRANSPOSE_ERROR = "transpose_error"  # 移项符号错误（演示预埋）
    CALC_ERROR = "calc_error"
    OTHER = "other"


class LookAtPageResult(BaseModel):
    """function_response of look_at_page。识题 or 读草稿：原样转写纸面文本，不解题、不补全。"""

    kind: VisionKind = VisionKind.LOOK_AT_PAGE
    text: str = Field(..., description="画面中纸面文本的原样转写")
    confidence: float = Field(..., ge=0.0, le=1.0)


class CheckDraftResult(BaseModel):
    """function_response of check_draft。只定位第一处错误「行+类型」，不报正确答案。"""

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
            raise ValueError("verdict=found_error 必须给出 error_line（契约八）")
        return self


class ObserveResult(BaseModel):
    """function_response of observe。穿搭/即兴物体：一句客观描述，只陈述所见。"""

    kind: VisionKind = VisionKind.OBSERVE
    description: str
    confidence: float = Field(..., ge=0.0, le=1.0)
