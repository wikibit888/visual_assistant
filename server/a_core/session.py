"""A · 会话级编排入口（PRD §7.1：turn_id 分配 + 工作记忆生命周期归 A）。

一个 WS 连接 = 一个 Session 实例。Session 是装配层（main.py）与编排核心（orchestrator）
之间的会话边界：
  - 持会话级 WorkingMemoryStore（仅内存、绝不落盘，隐私基线 PRD §1.5/§7.7）；
  - 分配连内唯一递增 turn_id（编排核心权属，PRD §7.1——装配层不再自造，铁律下沉到 A）；
  - asr.final → orchestrator.run_turn（编排 loop）→ 把每个 TtsSay 裹成 Envelope(tts.say,
    voice) 下行（A 只发 tts.say 给 B，铁律2）。

跨模块只走信封（铁律1）：本类对外仅产出 Envelope；不 import 其它模块内部对象，
只依赖 contracts（信封/枚举/voice）+ 同模块 A 的 orchestrator / WorkingMemoryStore。
"""

from __future__ import annotations

import itertools
import time
from typing import Optional

from contracts import Channel, Envelope, MessageType
from contracts.voice import AsrFinal
from server.a_core import orchestrator
from server.a_core.working_memory_store import WorkingMemoryStore


class Session:
    """单 WS 连接一实例：持会话级 WorkingMemoryStore + turn_id 分配器（A 权属，PRD §7.1）。"""

    def __init__(self, cfg: Optional[dict] = None) -> None:
        self.cfg = cfg
        # 会话级工作记忆（仅内存）：编排 loop 经此读写 mode、承载 memory_* 工具的 KV。
        self.store = WorkingMemoryStore()
        # 连内唯一递增 turn_id 序号（从 1 起，不跨连——与既有装配层占位口径一致）。
        self._turn_seq = itertools.count(1)

    def next_turn_id(self) -> str:
        """分配下一个 turn_id（格式 t-NNNNNN，连内唯一递增）。A 权属，装配层不得自造。"""
        return f"t-{next(self._turn_seq):06d}"

    async def handle_asr_final(
        self, asr_final: AsrFinal, cfg: Optional[dict] = None
    ) -> list[Envelope]:
        """asr.final → run_turn（编排 loop）→ 每个 TtsSay 裹成 Envelope(tts.say, voice) 下行。

        run_turn 收会话级 store（memory_* 工具据此绑定本会话）。返回的 tts.say 已过 A 的
        确定性护栏闸门（铁律3/5）——A 只发 tts.say 给 B（铁律2）。turn_id 沿用入参回合号
        （同回合所有消息共享同一 turn_id，契约一）。
        """
        says = await orchestrator.run_turn(asr_final, self.store, cfg or self.cfg)
        return [
            Envelope(
                type=MessageType.TTS_SAY,
                ts=int(time.time() * 1000),
                turn_id=asr_final.turn_id,
                channel=Channel.VOICE,
                payload=say.model_dump(),
            )
            for say in says
        ]

    def discard(self) -> None:
        """会话结束：丢弃工作记忆（仅内存，无文件需清理）。隐私基线——绝不落盘。"""
        self.store.discard()
