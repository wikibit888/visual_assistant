"""中继 · 工具执行体路由 + 视觉预算（契约·工具；PRD §1.4 / §5）。**确定性绿层，模型碰不到。**

Live 模型发 `function_call(name, args)` → 后端经此派发到工具执行体 → 把结构化结果作为
`function_response` 回灌 Live 会话。两类确定性在这里落地（PRD §1.4「代码决定怎么做到/做几次」）：

  ① 视觉抓帧次数封顶：单题 ≤ config.session.vision_budget_per_problem（识题1 + 批改≤2）。
     触顶 → 不再抓帧，回一个「我已看过，念给我听」语义的结果（PRD §5 抓帧超预算）。
  ② 失败/低置信：工具执行体返回带 confidence 的诚实结果，绝不编造（低置信如何措辞 = 提示词约束）。

视觉工具需要「当前帧」，但摄像头在客户端：本层经 `acquire_frame` 回调发起 frame.request/response
往返（live_bridge 注入该回调）。weather_get 无需帧。

骨架级（新 M0）：路由 + 预算计数为确定性实现；真实工具调用经 server.tools.*（其内部按
MOCK_VISION/MOCK_WEATHER 脱依赖）。`VisionBudget` 的「单题」边界由 live_bridge 在新识题时 reset。
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

from contracts.tools import ToolName
from contracts.vision import VisionKind

# 视觉工具 → 该工具要的帧 kind（acquire_frame 用以告知客户端取景提示）。
_VISION_KIND = {
    ToolName.LOOK_AT_PAGE.value: VisionKind.LOOK_AT_PAGE,
    ToolName.CHECK_DRAFT.value: VisionKind.CHECK_DRAFT,
    ToolName.OBSERVE.value: VisionKind.OBSERVE,
}

# acquire_frame 回调签名：给定 VisionKind → 异步取回当前帧（JPEG 字节）。由 live_bridge 注入。
AcquireFrame = Callable[[VisionKind], Awaitable[bytes]]


class VisionBudget:
    """单题视觉调用计数（确定性绿层，PRD §5）。live_bridge 在新识题时 reset()。"""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.used = 0

    def reset(self) -> None:
        self.used = 0

    def exhausted(self) -> bool:
        return self.used >= self.budget

    def charge(self) -> None:
        self.used += 1


async def dispatch(
    name: str,
    args: dict,
    *,
    acquire_frame: AcquireFrame,
    budget: Optional[VisionBudget],
    cfg: dict,
) -> dict:
    """派发一个 function_call → 工具执行体 → 返回结构化结果 dict（回作 function_response）。

    name：contracts.ToolName 之一（越界即不在白名单 → KeyError，守底；声明时已限定）。
    视觉工具：先查预算 → 经 acquire_frame 取当前帧 → 调 server.tools.vision_tools.*。
    weather_get：调 server.tools.weather.weather_get（lat/lon 缺省由执行体回落默认城市）。
    """
    if name not in {t.value for t in ToolName}:
        raise KeyError(f"function_call 不在工具白名单（契约·工具）：{name}")

    if name in _VISION_KIND:
        if budget is not None and budget.exhausted():
            # 抓帧超预算（PRD §5）：不再抓帧，回「已看过」语义结果，让模型改请用户口述。
            return {"over_budget": True, "note": "已达单题视觉预算，请用户念给我听"}
        kind = _VISION_KIND[name]
        frame = await acquire_frame(kind)
        if budget is not None:
            budget.charge()
        from server.tools import vision_tools

        if name == ToolName.LOOK_AT_PAGE.value:
            return (await vision_tools.look_at_page(frame, cfg)).model_dump()
        if name == ToolName.CHECK_DRAFT.value:
            return (await vision_tools.check_draft(frame, cfg)).model_dump()
        return (await vision_tools.observe(frame, args.get("hint"), cfg)).model_dump()

    # weather_get
    from server.tools import weather

    return (await weather.weather_get(args.get("lat"), args.get("lon"), cfg)).model_dump()
