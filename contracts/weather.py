"""weather.get 工具 I/O（契约八工具注册表的一部分；PRD §4.1 / §7.3）。

天气走 Open-Meteo（无 key）+ 城市/小时缓存 + 写死兜底（config `weather`）。
自动定位失败/超时/拒绝 → 静默回落 fallback_city，绝不阻塞。MOCK_WEATHER=1 返回兜底。
不播报天气，只用于穿搭建议（PRD §4.1）。
"""

from typing import Optional

from pydantic import BaseModel, Field


class WeatherGetArgs(BaseModel):
    lat: float
    lon: float


class WeatherResult(BaseModel):
    summary: str = Field(..., description="天气概述，如 'light_rain'")
    temp: float
    precip: float = Field(0.0, description="降水概率/量")
    wind: Optional[float] = None
    source: str = Field("open-meteo", description="open-meteo | fallback | mock")
