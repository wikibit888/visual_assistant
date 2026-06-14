"""契约九 · weather_get 工具 I/O（PRD §3 工具注册表 / §5，形态 = Pydantic）。

天气走 Open-Meteo（无 key）+ 城市/小时缓存 + 写死兜底（config `weather`）。定位由
`navigator.geolocation` 在客户端取，随 function_call 注入 lat/lon；失败/超时/拒绝 → 工具执行体
静默回落 `fallback_city`，绝不阻塞（PRD §5 定位失败）。`MOCK_WEATHER=1` 直接返回兜底。

不向用户播报天气数字——只用于推导「具体到行动」的穿搭/出行建议（加件外套 / 带伞，PRD §2 生活）。
"""

from typing import Optional

from pydantic import BaseModel, Field


class WeatherGetArgs(BaseModel):
    """function_call args of weather_get。lat/lon 缺省 → 工具执行体回落默认城市（PRD §5）。"""

    lat: Optional[float] = None
    lon: Optional[float] = None


class WeatherResult(BaseModel):
    """function_response of weather_get。"""

    summary: str = Field(..., description="天气概述，如 'light_rain'")
    temp: float
    precip: float = Field(0.0, description="降水概率/量")
    wind: Optional[float] = None
    source: str = Field("open-meteo", description="open-meteo | fallback | mock")
