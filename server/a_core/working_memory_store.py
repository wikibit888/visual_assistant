"""A · 工作记忆运行时（契约十）。**仅内存、绝不落盘**（PRD §1.5 / §7.7，隐私基线）。

持有单会话状态，由 A 编排核心实例化、读写；会话结束即丢弃，绝不持久化。
本对象是 A 内部对象，不经总线外泄、不被其它模块 import（铁律 1）。

阻抗不匹配（刻意处理，契约十零改动）：
  memory_note / memory_recall 的入参是「通用 KV」（key/value，见契约十
  MemoryNoteArgs / MemoryRecallArgs），但契约十 WorkingMemory 是结构化字段、
  没有通用 KV 槽。故本 store 同时持有：
    - self.memory: WorkingMemory   结构化（current_mode / active_problem / ...），供编排器读写；
    - self._notes: dict[str, Any]  store 层的通用内存 KV，承载 note/recall 的自由 KV，
                                   **不放进 WorkingMemory 模型**（不改契约）。
"""

from __future__ import annotations

from typing import Any

from contracts.working_memory import (
    MemoryNoteArgs,
    MemoryRecallArgs,
    WorkingMemory,
)


class WorkingMemoryStore:
    """单会话工作记忆容器。仅内存：构造与读写均零文件 / 零磁盘操作。"""

    def __init__(self) -> None:
        # 结构化工作记忆（契约十）：默认空记忆（current_mode=open 等由 WorkingMemory 默认）。
        self.memory: WorkingMemory = WorkingMemory()
        # store 层通用 KV（承载 memory_note/recall 的自由 KV），不进 WorkingMemory 模型。
        self._notes: dict[str, Any] = {}
        # 不做任何 open()/Path.write*/json.dump(到文件) —— 隐私基线：绝不落盘。

    async def memory_note(self, key: str, value: Any = None) -> dict:
        """工具 memory_note（§7.3）：校验入参 → 写通用 KV → 返回 ack。

        入参经契约十 MemoryNoteArgs 校验（非法 → ValidationError）。
        out_schema = (ack)。覆盖写：同 key 再 note 取最新值。
        """
        args = MemoryNoteArgs(key=key, value=value)
        self._notes[args.key] = args.value
        return {"ok": True, "key": args.key}

    async def memory_recall(self, key: str) -> Any:
        """工具 memory_recall（§7.3）：校验入参 → 取回通用 KV 的 value。

        入参经契约十 MemoryRecallArgs 校验。out_schema = (value)；
        缺失 key 返回 None。
        """
        args = MemoryRecallArgs(key=key)
        return self._notes.get(args.key)

    def discard(self) -> None:
        """会话结束丢弃：清空结构化记忆 + 通用 KV（仅内存，无文件需清理）。

        实例本身随会话 GC；本方法供需要复用实例时显式归零。
        """
        self.memory = WorkingMemory()
        self._notes.clear()
