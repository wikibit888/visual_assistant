"""契约一 · 控制信封（Live 版，PRD §3 / §4.1）。

新架构下唯一的「跨进程通信形态」是 **客户端 ⇄ 后端单 WebSocket**。该 WS 上有两类帧：
  - 二进制帧 = 音频（PCM16 上行 / PCM24 下行）——高频、裸字节、**不裹信封**。
  - 文本帧   = 控制/事件消息——一律是本 `Envelope`（JSON）。

后端再用供应商 SDK 持有 Live 会话（Gemini Live / OpenAI realtime），把音频双向泵给模型、
把 `function_call` 派发给工具执行体——那一段不在本契约面内（由 provider SDK 约束）。
本契约面 = 客户端与后端之间的协议，是前后端并行开发的真理来源。

字段最小化：Live 模型自己管理「轮次」，故信封不再带 turn_id；需要请求/响应配对的消息
（如 frame.request/response）在 payload 里带自己的 `request_id`。
"""

from pydantic import BaseModel, Field

from .protocol import Channel, MessageType

SCHEMA_VERSION = "0.1"


class Envelope(BaseModel):
    """`{type, ts, channel, payload}` + schema_version。客户端⇄后端控制帧的统一外壳。

    payload 的具体 schema 由 `type` 对应的契约模型负责校验（见 CONTRACTS.md 的 type→model 映射）；
    本层只保证信封结构本身。音频走二进制帧、不经本信封。
    """

    type: MessageType
    ts: int = Field(..., description="epoch 毫秒")
    channel: Channel
    payload: dict = Field(
        default_factory=dict,
        description="按 type 由对应契约模型校验；本层只保证信封结构",
    )
    schema_version: str = SCHEMA_VERSION
