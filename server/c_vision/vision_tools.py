"""C · vision.* 工具实现（契约三）。M0 骨架。MOCK_VISION=1 读 fixture，可独立运行。

read_problem → ReadProblemResult{problem_text, confidence}
check_draft  → CheckDraftResult{verdict(四值), error_line?, error_type?, confidence}
observe      → ObserveResult{description, confidence}
confidence 交由 A 的置信门控裁决，C 不自行决定是否播报。
"""

from typing import Optional

# from contracts import ReadProblemResult, CheckDraftResult, ObserveResult


async def read_problem():
    raise NotImplementedError("M1/M2：识题（gemini 多模态 / MOCK_VISION 读 fixture）")


async def check_draft():
    raise NotImplementedError("M2：批改（四值 verdict）")


async def observe(hint: Optional[str] = None):
    raise NotImplementedError("M3：穿搭/即兴物体识别")
