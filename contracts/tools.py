"""契约十 · 工具注册表（PRD §3，形态 = Pydantic + 文档）。

新架构下「工具」= Live 模型可发起的 `function_call`；执行体是后端的**确定性代码**（PRD §1.4
职责分界：模型决定「做什么」，代码决定「怎么做到、做几次、做不到/何时放行」）。注册表是
后端给 Live 会话声明可用函数、并据此派发 function_call 的真理来源。

白名单四工具（PRD §3）。**无执行类工具**——订外卖/读屏/控设备一律无对应函数 → 模型按提示词
「帮不上」（PRD §2 / §5 越界请求）。坐姿 `posture.alert` **不在表内**：它是客户端推给模型的事件，
不是模型能拉的工具（PRD §3 / §4.1）。「说话」也不是工具，是 Live 模型的原生输出。

mode → 工具子集（后端按 session.mode 给 Live 会话声明，收窄是 profile 不是流水线）：
  - open：四工具皆可（基座）。
  - learning：look_at_page / check_draft（穿搭/天气一般不需要）。
  - life：observe / weather_get（识题/批改一般不需要）。
  子集是软收窄（提示 + 声明），不是硬护栏——误判风险 PRD §5「模式误判」知情接受。
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ToolName(str, Enum):
    """工具白名单（PRD §3）。Live 会话只声明这些函数。"""

    LOOK_AT_PAGE = "look_at_page"
    CHECK_DRAFT = "check_draft"
    OBSERVE = "observe"
    WEATHER_GET = "weather_get"


class ToolSpec(BaseModel):
    """工具注册表条目。args/result 为对应 schema 的人读标注（声明 function 用）。"""

    name: ToolName
    intent: str = Field(..., description="模型何时发起此 function_call 的意图（写进 function 声明）")
    args_schema: str = Field(..., description="入参 schema（契约模型名或 '(无入参)'）")
    result_schema: str = Field(..., description="function_response schema（契约模型名）")
    mock_env: Optional[str] = None


# 工具注册表（PRD §3）——纯数据；后端据此向 Live 会话声明函数并派发 function_call。
TOOL_REGISTRY: dict[str, ToolSpec] = {
    "look_at_page": ToolSpec(
        name=ToolName.LOOK_AT_PAGE,
        intent="需要看一眼用户指的纸面（识题或读草稿原文）时",
        args_schema="(无入参；执行体即刻抓当前帧)",
        result_schema="vision.LookAtPageResult",
        mock_env="MOCK_VISION",
    ),
    "check_draft": ToolSpec(
        name=ToolName.CHECK_DRAFT,
        intent="需要看学生写得对不对（只定位错误行+类型，不报答案）时",
        args_schema="(无入参；执行体即刻抓当前帧)",
        result_schema="vision.CheckDraftResult",
        mock_env="MOCK_VISION",
    ),
    "observe": ToolSpec(
        name=ToolName.OBSERVE,
        intent="需要看画面里的东西（穿搭/随手物体）时",
        args_schema="{hint?: str}",
        result_schema="vision.ObserveResult",
        mock_env="MOCK_VISION",
    ),
    "weather_get": ToolSpec(
        name=ToolName.WEATHER_GET,
        intent="需要天气以推导穿搭/出行的具体动作建议时（不播报数字）",
        args_schema="weather.WeatherGetArgs",
        result_schema="weather.WeatherResult",
        mock_env="MOCK_WEATHER",
    ),
}

# mode → 可用工具子集（软收窄，PRD §2/§3）。后端按 session.mode 取此子集声明给 Live 会话。
MODE_TOOLSETS: dict[str, list[str]] = {
    "open": ["look_at_page", "check_draft", "observe", "weather_get"],
    "learning": ["look_at_page", "check_draft"],
    "life": ["observe", "weather_get"],
}
