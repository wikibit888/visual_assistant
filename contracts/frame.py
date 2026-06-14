"""契约五 · 摄像头单帧往返（PRD §3 工具注册表 / §4.1，形态 = Pydantic）。

视觉是**按需抓帧**（工具触发），不是连续视频流（PRD §4.1「按需抓帧」+ 视觉预算计数）。
摄像头在客户端，识别在后端，故每次视觉工具触发都有一次跨进程抓帧往返：

  Live 模型 --function_call(look_at_page/check_draft/observe)--> 后端工具执行体
  后端 --frame.request{request_id, kind}--> 客户端（要当前帧）
  客户端抓 <video> 当前帧 → JPEG → base64 --frame.response{request_id, jpeg_base64}--> 后端
  后端把帧交视觉识别（gemini-2.5-flash）→ {…, confidence} --function_response--> Live 模型

为何用 base64 JSON 而非二进制帧：摄像头帧低频（仅工具触发、受视觉预算封顶），base64 开销可接受，
且能与高频音频二进制帧明确区分（二进制帧恒为音频，文本帧恒为控制）——避免帧类型歧义。
"""

from pydantic import BaseModel, Field

from .vision import VisionKind


class FrameRequest(BaseModel):
    """payload of `frame.request` —— 后端向客户端要一帧（某个视觉工具触发）。"""

    request_id: str = Field(..., description="本次抓帧的配对 id，frame.response 必须回带")
    kind: VisionKind = Field(..., description="哪个视觉工具要的帧（决定客户端可加的取景提示）")


class FrameResponse(BaseModel):
    """payload of `frame.response` —— 客户端回传抓到的当前帧。"""

    request_id: str = Field(..., description="对应 frame.request.request_id")
    jpeg_base64: str = Field(..., description="当前视频帧的 JPEG，base64 编码（不含 data: 前缀）")
