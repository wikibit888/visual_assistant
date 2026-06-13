"""契约九 · 答案护栏（可选，非硬红线；PRD §3.1.5 / §7.7，形态 = 文档）。

demo 默认关（config `answer_guard.enabled=false`）。启用时（PRD §3.1.5）：
① 识题时模型顺带解出 solved_answer（不播报、不入明文日志，存工作记忆契约十）。
② 候选回复出站前 **正则组合拦截**（触发词 list ∩ 数值组合命中才触发 → 替换为追问话术）；
   纯字符串、无 LLM、无延迟。
③ 误杀专测含生活语境数字（温度/年龄/楼层）——见 M4/M5。
④ **拦截层在循环外**（护栏不可被 planner 覆盖，PRD §7.1 铁律）。
可选增强（不默认）：方程题 sympy 本地二次校验（config `answer_guard.sympy_recheck`）。

触发词表 / 数值组合正则由 E（server/e_skills/）提供；本契约只定判定口径与产出形态。
"""

from typing import Optional

from pydantic import BaseModel, Field

from .orchestration import Mode


class AnswerGuardConfig(BaseModel):
    """对应 config.yaml `answer_guard` 段。"""

    enabled: bool = False
    scope_modes: list[Mode] = Field(default_factory=lambda: [Mode.LEARNING])
    sympy_recheck: bool = False


class GuardDecision(BaseModel):
    """护栏对一条候选回复的裁决。triggered=True → 用 replacement_text 替换原文。"""

    triggered: bool
    replacement_text: Optional[str] = Field(
        None, description="命中时的追问话术（E 提供）；纯字符串替换"
    )
