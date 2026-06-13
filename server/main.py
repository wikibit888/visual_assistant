"""后端入口 · FastAPI 单 WebSocket（PRD §7.1）。M1-01：装配 + 信封收发，禁业务逻辑。

唯一 WS 端点 /ws 承载契约一信封（Envelope）的双向收发（JSON 文本帧）：
  上行 = web → asr.final / vision 回执 / posture.alert / control / tts.ack
  下行 = handler → tts.say/stop / gap.open / vision.request（M1-02+ 由各模块产）
路由：按 Envelope.channel 分发（server.ws_router），跨模块只走信封（铁律 §7.1）。
M1-01 各 channel 占位回环；真实 handler 由 A/B/C/E 后续里程碑经 ws_router.register 挂载。

M1-06b 增量 · 音频上行传输层（同一 /ws，按帧形态分流）：
  - 文本帧 = JSON 信封 → 现有逻辑原样不变（Envelope 校验 → ws_router.route → 回送）。
  - 二进制帧 = 一个用户回合的音频（前端 MediaRecorder blob）。音频是 B 模块前后端
    「内部传输」、非跨模块通信，故不裹信封（铁律①）。后端收音频 → 经
    server.b_voice.asr_adapter.transcribe 得 contracts.AsrFinal → 在此装配层裹成
    Envelope(type=asr.final, channel=voice) → 投「现有 ws_router.route()」总线。
    asr.final 才是跨模块消息，必须走信封（契约一 / 契约二 AsrFinal）。M1 阶段 voice
    channel 仍是占位回环 handler（asr.final 经总线回环上来即验通）；M2 由 A 的 run_turn
    经 ws_router.register(voice, ...) 接管消费——本入口不碰编排逻辑。
"""

from __future__ import annotations

import itertools
import logging
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from contracts import Channel, ConfigPushPayload, Envelope, MessageType
from contracts.config_schema import load_config
from server import ws_router
from server.b_voice import asr_adapter

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
        """单 WS：文本帧=信封路由；二进制帧=音频上行 → asr.final → 同一总线。"""
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

        # turn_id 占位计数器（连级递增，格式 t-NNNNNN）。
        # ⚠ M2 由 A 统一分配 turn_id（编排核心权属，PRD §7.1）——此处仅装配层兜底，
        #   B 不越权自决回合号；A run_turn 接管后本计数器作废。
        turn_seq = itertools.count(1)

        try:
            while True:
                # 单次 receive 同时拿文本/二进制：按帧形态分流（不破坏现有文本信封路径）。
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(msg.get("code", 1000))

                text = msg.get("text")
                if text is not None:
                    # —— 文本帧 = JSON 信封：现有逻辑原样不变 ——
                    try:
                        env = Envelope.model_validate_json(text)
                    except Exception as e:  # 非法信封：仅记录并跳过，不拖垮连接
                        log.warning("丢弃非法信封：%s", e)
                        continue
                    for out in await ws_router.route(env):
                        await ws.send_text(out.model_dump_json())
                    continue

                audio = msg.get("bytes")
                if audio is not None:
                    # —— 二进制帧 = 音频（B 内部传输，不裹信封）→ ASR → asr.final 上总线 ——
                    # turn_id：M1 装配层占位递增（M2 由 A 分配，不让 B 越权——见上注）。
                    turn_id = f"t-{next(turn_seq):06d}"
                    asr_final = await asr_adapter.transcribe(audio, turn_id, cfg)
                    # asr.final 是跨模块消息 → 必须裹信封（契约一/契约二），经现有 ws_router.route。
                    env = Envelope(
                        type=MessageType.ASR_FINAL,
                        ts=int(time.time() * 1000),
                        turn_id=turn_id,
                        channel=Channel.VOICE,
                        payload=asr_final.model_dump(),
                    )
                    for out in await ws_router.route(env):
                        await ws.send_text(out.model_dump_json())
        except WebSocketDisconnect:
            log.info("ws 连接断开")

    return app


# uvicorn server.main:app 入口（模块级 app）。
app = create_app()
