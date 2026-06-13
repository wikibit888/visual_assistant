"""契约一 · 消息信封（PRD §7.7，形态 = Pydantic）。

跨模块唯一通信形态。任何模块发到总线（单 WebSocket）的消息都必须是 Envelope。
payload 的具体 schema 由对应契约模型负责校验（见 CONTRACTS.md 的 type→model 映射）。
"""

from pydantic import BaseModel, Field

from .message_types import Channel, MessageType

SCHEMA_VERSION = "0.1"


class Envelope(BaseModel):
    """`{type, ts, turn_id, channel, payload}` + schema_version（满足契约三「带 version」）。"""

    type: MessageType
    ts: int = Field(..., description="epoch 毫秒")
    turn_id: str = Field(
        ...,
        description='每个用户回合递增，形如 "t-000123"；同一回合所有消息共享同一 turn_id',
    )
    channel: Channel
    payload: dict = Field(
        default_factory=dict,
        description="按 type 由对应契约模型校验；本层只保证信封结构",
    )
    schema_version: str = SCHEMA_VERSION
