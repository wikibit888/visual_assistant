"""单 WS 信封路由（M1-01 地基）· 按 Envelope.channel 分发到 channel handler。

职责（仅装配/路由，无业务逻辑）：
- 维护 channel → handler 分发表（voice / vision / weather / posture / control / orchestrator）。
- handler 形态：`async (Envelope) -> list[Envelope]`，返回 0..n 个需回送对端的信封。
- M1-01 六个 channel 一律占位（记录 + 传输级回环），真实 handler 由 A/B/C/E 在后续
  里程碑经 `register()` 挂载——不改本文件、不改 main.py（铁律：跨模块只走信封总线）。

铁律守卫（PRD §7.1）：本路由只搬运信封，不臆造跨模块语义——
`gap.open` 仍只由 A 产、`tts.*` 只由 A 发往 B、`posture.alert` 只由 D 产；
占位 handler 只做「收到即回环同一信封」的传输级校验，绝不新铸上述类型。
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from contracts import Channel, Envelope

log = logging.getLogger("va.ws_router")

# 收到一个入站信封 → 返回 0..n 个出站信封（回送给对端）。
Handler = Callable[[Envelope], Awaitable[list[Envelope]]]


async def _echo_placeholder(env: Envelope) -> list[Envelope]:
    """M1-01 占位 handler：记录并把原信封回环，验证双向传输。

    传输级回环（同 type / turn_id / payload 原样返回），非业务逻辑、非新铸契约消息；
    后续里程碑用 `register()` 覆盖对应 channel 后即失效。
    """
    log.info(
        "ws_router 占位收发 channel=%s type=%s turn_id=%s",
        env.channel.value,
        env.type.value,
        env.turn_id,
    )
    return [env]


# 分发表：六个 channel 全覆盖（Channel 为闭集）。缺 channel 时显式告警，不静默吞消息。
_HANDLERS: dict[Channel, Handler] = {ch: _echo_placeholder for ch in Channel}


def register(channel: Channel, handler: Handler) -> None:
    """挂载某 channel 的真实 handler（A/B/C/E 后续里程碑用，免改 main.py）。"""
    _HANDLERS[channel] = handler


async def route(env: Envelope) -> list[Envelope]:
    """按 Envelope.channel 分发到 handler；返回需回送的信封列表。"""
    handler = _HANDLERS.get(env.channel)
    if handler is None:  # Channel 是闭集，理论不达；防御以免静默丢消息
        log.warning(
            "ws_router 无 channel=%s 的 handler，丢弃 type=%s",
            env.channel.value,
            env.type.value,
        )
        return []
    return await handler(env)
