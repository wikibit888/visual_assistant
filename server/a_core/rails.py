"""A · rails 切换（PRD §7.2 / §8）。M0 骨架。

rails 与 agentic **同代码、config 切换**（config.rails.enabled）。
切 rails：注入 forced_tool_sequence（学习/生活），max_tool_rounds→0，agent 只填语言。
open 不套 rails；超出工具序 → 优雅收口分支（"帮不上，回到作业/穿搭"）。
M0 只保证 config 钩子就位；切换逻辑 M2 实现，M4 彩排一次。
"""


def is_railed(cfg) -> bool:
    """读 config.rails.enabled。M2 实现接线。"""
    raise NotImplementedError("M2")
