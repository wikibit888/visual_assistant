"""E · 提示词与话术（PRD §7.1 / §3.1.5 / §3.2）。M0 占位，文案 M1+ 填。

约定：所有 prompt 输出必须符合契约（planner → contracts.PlannerOutput JSON schema）；
E 只提供「措辞与策略」，不得做意图分类/路由（铁律）。话术模板随机抽取，坐姿模板不调 LLM。
"""

# 占位键，M1+ 填具体文案（禁把阈值/模型名写进 prompt 硬编码，引用 config）
PLANNER_SYSTEM = ""        # 编排 planner system prompt（温度0+结构化+工具白名单）
GUIDE_STYLE = ""           # 引导帮解（方向级/操作级/示范级阶梯）
OUTFIT_STYLE = ""          # 穿搭建议（结合天气，具体到行动）
SUMMARY_STYLE = ""         # 口头小结（做了几道、卡点、提醒坐姿几次）
OPEN_STYLE = ""            # 开放对话（无预设 + 诚实兜底 + 期望管理话术）
POSTURE_TEMPLATES: list[str] = []   # 坐姿提醒单级话术模板（随机抽取，零 LLM）
ANSWER_GUARD_TRIGGERS: list[str] = []  # 答案护栏触发词（配合数值组合正则）
