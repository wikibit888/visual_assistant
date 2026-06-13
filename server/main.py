"""后端入口 · FastAPI 单 WebSocket（PRD §7.1）。M0 仅骨架，禁业务逻辑。

唯一 WS 端点承载契约一信封的双向收发。M1 起接：
  上行 = web → asr.final / vision.request 回执 / posture.alert / control.stop
  下行 = A → tts.say/stop / gap.open / vision.request
路由：按 Envelope.channel 分发到对应模块；跨模块只走信封（铁律 PRD §7.1）。
"""

# from fastapi import FastAPI, WebSocket
# from contracts import Envelope

# app = FastAPI(title="Visual Assistant v0.1")


def create_app():
    """构建 FastAPI app 并挂载 /ws 端点。M1 实现。"""
    raise NotImplementedError("M1 语音链路：FastAPI app + /ws 信封收发")


# @app.websocket("/ws")
# async def ws_endpoint(ws: WebSocket):
#     """单 WS：收发 contracts.Envelope。M1 实现。"""
#     raise NotImplementedError("M1")
