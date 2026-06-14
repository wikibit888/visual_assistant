"""后端入口 · FastAPI 单 WebSocket 中继（Live 版，PRD §3 / §4）。

唯一 WS 端点 /ws 承载客户端 ⇄ 后端协议（契约一）：
  - 文本帧 = 控制 Envelope（JSON）：session.start/update · input.activity_* · frame.response ·
    posture.alert · text.input → 交 LiveBridge 处理；后端回 session.ready · transcript ·
    tool.activity · interrupted · frame.request · error 等。
  - 二进制帧 = 音频（PCM16 上行）：直接喂 LiveBridge（B 内部传输，不裹信封）。

后端只是中继 + 工具执行体（确定性）；编排/ASR/TTS/打断全在 LiveBridge 持有的供应商 Live 会话里。
建连即下发 config.push（前端阈值快照，posture+voice 子树；前端不自带魔数——契约·配置）。
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from contracts import Channel, ConfigPushPayload, Envelope, MessageType, SessionStart
from contracts.config_schema import load_config
from server.relay.live_bridge import LiveBridge

log = logging.getLogger("va.main")


def create_app() -> FastAPI:
    """构建 FastAPI app 并挂载 /ws（中继）+ /healthz。"""
    app = FastAPI(title="Visual Assistant v0.1 (Live)")

    cfg = load_config()  # 进程级加载一次；缺 config.yaml 抛清晰错误（基线必备文件）
    config_push_payload = ConfigPushPayload(
        posture=cfg.get("posture", {}),
        voice=cfg.get("voice", {}),
    ).model_dump()

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        log.info("ws 连接建立")

        async def send_event(env: Envelope) -> None:
            await ws.send_text(env.model_dump_json())

        async def send_audio(pcm: bytes) -> None:
            await ws.send_bytes(pcm)

        # 建连即下发 config.push（control 通道；前端 init 据此装配 B/D，不自带魔数）。
        await send_event(
            Envelope(
                type=MessageType.CONFIG_PUSH,
                ts=int(time.time() * 1000),
                channel=Channel.CONTROL,
                payload=config_push_payload,
            )
        )

        bridge = LiveBridge(cfg, send_event=send_event, send_audio=send_audio)
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(msg.get("code", 1000))

                text = msg.get("text")
                if text is not None:
                    try:
                        env = Envelope.model_validate_json(text)
                    except Exception as e:  # 非法信封：记录并跳过，不拖垮连接
                        log.warning("丢弃非法信封：%s", e)
                        continue
                    if env.type == MessageType.SESSION_START:
                        await bridge.start(SessionStart.model_validate(env.payload))
                    else:
                        await bridge.on_client_event(env)
                    continue

                audio = msg.get("bytes")
                if audio is not None:
                    await bridge.on_client_audio(audio)
        except WebSocketDisconnect:
            log.info("ws 连接断开")
            await bridge.aclose()  # 关 Live 会话；会话记忆在 Live 侧，随会话销毁（无落盘）

    return app


# uvicorn server.main:app 入口（模块级 app）。
app = create_app()
