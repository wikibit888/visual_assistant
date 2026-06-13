"""A · 工作记忆运行时（契约十）。**仅内存、不落盘**（PRD §1.5 / §7.7）。M0 骨架。

持有单会话的 contracts.WorkingMemory；memory.note/recall 读写本结构。
会话结束即丢弃，绝不持久化（隐私基线）。
"""

# from contracts import WorkingMemory


class WorkingMemoryStore:
    """单会话工作记忆容器。M2 实现读写。"""

    def __init__(self):
        # self.memory = WorkingMemory()
        raise NotImplementedError("M2")
