"""后端入口 · FastAPI 单 WebSocket 中继（Live 版，PRD §3 / §4）。

唯一 WS 端点 /ws 承载客户端 ⇄ 后端协议（契约一）：
  - 文本帧 = 控制 Envelope（JSON）：session.start/update · input.activity_* · frame.response ·
    posture.alert · text.input → 交 LiveBridge 处理；后端回 session.ready · transcript ·
    tool.activity · interrupted · frame.request · error 等。
  - 二进制帧 = 音频（PCM16 上行）：直接喂 LiveBridge（B 内部传输，不裹信封）。

后端只是中继 + 工具执行体（确定性）；编排/ASR/TTS/打断全在 LiveBridge 持有的供应商 Live 会话里。
建连即下发 config.push（前端阈值快照，posture+voice+audio 子树；前端不自带魔数——契约·配置）。

开发便利：本 app 还把 `web/` 目录静态托管在 `/`（同源），故 `uvicorn server.main:app` 一条命令即同时
起「前端页面 + /ws 中继」——浏览器开 http://localhost:8000/ 即是引导页（前端的 ws://location.host/ws
因同源自然指向本服务）。生产可改用独立静态服务器，不影响协议。
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from contracts import (
    Channel,
    ConfigPushPayload,
    Degradation,
    Envelope,
    ErrorEvent,
    MessageType,
    SessionStart,
)
from contracts.config_schema import load_config
from server.relay.live_bridge import LiveBridge

# 前端目录（server/main.py → parents[1] = 仓库根 → web/）。
_WEB_DIR = Path(__file__).resolve().parents[1] / "web"

log = logging.getLogger("va.main")


def create_app() -> FastAPI:
    """构建 FastAPI app 并挂载 /ws（中继）+ /healthz + `/` 静态托管 web/（开发便利）。"""
    app = FastAPI(title="Visual Assistant v0.1 (Live)")

    cfg = load_config()  # 进程级加载一次；缺 config.yaml 抛清晰错误（基线必备文件）
    _session_cfg = cfg.get("session", {}) or {}
    config_push_payload = ConfigPushPayload(
        posture=cfg.get("posture", {}),
        voice=cfg.get("voice", {}),
        audio={
            "in_sample_rate": _session_cfg.get("audio_in_sample_rate"),
            "out_sample_rate": _session_cfg.get("audio_out_sample_rate"),
        },
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
                audio = msg.get("bytes")
                try:
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
                    elif audio is not None:
                        await bridge.on_client_audio(audio)
                except WebSocketDisconnect:
                    raise
                except Exception:
                    # 单条消息处理出错（典型：Live 会话已断，再发音频朝已关 ws send）→ **绝不**让异常
                    # 冒泡拖垮整个 WS（否则前端只见 onclose=「连接断开」、无从优雅降级）。改为通知客户端
                    # 降级、保持连接（PRD §5/§8：字幕/文字输入兜底；刷新即重开 Live 会话）。
                    log.exception("处理客户端消息出错（连接保持）")
                    with contextlib.suppress(Exception):
                        await send_event(
                            Envelope(
                                type=MessageType.ERROR,
                                ts=int(time.time() * 1000),
                                channel=Channel.CONTROL,
                                payload=ErrorEvent(
                                    code="server_error",
                                    message="后端处理出错，可刷新重连或改用文字输入。",
                                    degradation=Degradation.FALLBACK_TEXT,
                                ).model_dump(),
                            )
                        )
        except WebSocketDisconnect:
            log.info("ws 连接断开")
        finally:
            # 无论如何都收尾：关 Live 会话、停泵（会话记忆在 Live 侧，随之销毁，无落盘）。
            await bridge.aclose()

    # 静态托管前端（**必须在 /healthz、/ws 之后挂**：显式路由先匹配，其余落到静态文件）。
    # html=True → `/` 返回 web/index.html；/src/*.js、/styles.css、/src/worklets/*.js 皆从 web/ 解析。
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
    else:  # 缺 web/（理论不达，基线必备）——只少了前端托管，/ws 中继不受影响
        log.warning("未找到前端目录 %s，跳过静态托管（/ws 仍可用）", _WEB_DIR)

    return app


# uvicorn server.main:app 入口（模块级 app）。
app = create_app()
