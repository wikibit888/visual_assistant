"""后端入口 · FastAPI 单 WebSocket（PRD §7.1）。M1-01：装配 + 信封收发，禁业务逻辑。

唯一 WS 端点 /ws 承载契约一信封（Envelope）的双向收发（JSON 文本帧）：
  上行 = web → asr.final / vision 回执 / posture.alert / control / tts.ack
  下行 = handler → tts.say/stop / gap.open / vision.request（M1-02+ 由各模块产）
路由：按 Envelope.channel 分发（server.ws_router），跨模块只走信封（铁律 §7.1）。
M1-01 各 channel 占位回环；真实 handler 由 A/B/C/E 后续里程碑经 ws_router.register 挂载。
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from contracts import Channel, ConfigPushPayload, Envelope, MessageType
from contracts.config_schema import load_config
from server import ws_router

log = logging.getLogger("va.main")


def create_app() -> FastAPI:
    """构建 FastAPI app 并挂载 /ws 端点（契约一信封收发）。"""
    app = FastAPI(title="Visual Assistant v0.1")

    # 建连即下发的前端配置快照：取 config.yaml 的 turn_state/posture 子树（契约七，零硬编码）。
    # 静态配置，进程级加载一次；缺 config.yaml 时 load_config 抛清晰错误（基线必备文件）。
    cfg = load_config()
    config_push_payload = ConfigPushPayload(
        turn_state=cfg.get("turn_state", {}),
        posture=cfg.get("posture", {}),
    ).model_dump()

    @app.get("/healthz")
    async def healthz() -> dict:
        """存活探针（运维用，非契约消息）。"""
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        """单 WS：收 contracts.Envelope → 按 channel 路由 → 回送 Envelope。"""
        await ws.accept()
        log.info("ws 连接建立")
        # 建连即下发 config.push（control 通道；建连尚无 turn → 哨兵 t-000000）。
        # A 唯一下发控制信息：仅前端阈值快照，不臆造 gap.open/tts.*/posture.alert（铁律）。
        await ws.send_text(
            Envelope(
                type=MessageType.CONFIG_PUSH,
                ts=int(time.time() * 1000),
                turn_id="t-000000",
                channel=Channel.CONTROL,
                payload=config_push_payload,
            ).model_dump_json()
        )
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    env = Envelope.model_validate_json(raw)
                except Exception as e:  # 非法信封：仅记录并跳过，不拖垮连接
                    log.warning("丢弃非法信封：%s", e)
                    continue
                for out in await ws_router.route(env):
                    await ws.send_text(out.model_dump_json())
        except WebSocketDisconnect:
            log.info("ws 连接断开")

    return app


# uvicorn server.main:app 入口（模块级 app）。
app = create_app()
