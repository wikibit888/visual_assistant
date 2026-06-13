"""契约五 · 错误降级（PRD §7.7，形态 = 注释/枚举）。

降级动作封闭取值。A 编排核心据此处置工具/planner 异常（PRD §8 降级表）：
  - RETRY         —— 受 config orchestration.vision_retry_max 约束的有限重试。
  - FALLBACK_TEXT —— 走降级话术（loop 触顶 / planner 超时 / 粘滞兜底）。
  - ABORT         —— 放弃本回合动作，维持现场景，不阻塞首响。
  - TOOL_FAIL     —— 工具失败信号 → planner 重规划或降级。
"""

from enum import Enum


class Degradation(str, Enum):
    RETRY = "RETRY"
    FALLBACK_TEXT = "FALLBACK_TEXT"
    ABORT = "ABORT"
    TOOL_FAIL = "TOOL_FAIL"
