"""工具 · weather_get 执行体（契约·天气；PRD §3 / §5）。

定位 → Open-Meteo（无 key）→ 坐标/小时缓存 → 写死兜底。失败/超时/定位拒绝 → 静默回落
config.weather.fallback_city，绝不阻塞（PRD §5）。MOCK_WEATHER=1 直接返回兜底。

不向用户播报数字——只供 Live 模型推导穿搭/出行的「具体动作」建议。
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from contracts.config_schema import load_config
from contracts.mock import is_mock
from contracts.weather import WeatherResult

log = logging.getLogger("va.weather")

# 请求超时（实现常量，非业务阈值）：弱网下宁可快回落兜底也不阻塞主链路（PRD §5 天气是「唯一牺牲位」）。
_REQUEST_TIMEOUT_S = 5.0
# 坐标粒度缓存：key=(round lat, round lon) → (过期 epoch 秒, 结果)。TTL 取 config.weather.cache_ttl_hours。
_CACHE: dict[tuple, tuple] = {}
# Open-Meteo WMO weather_code → 粗粒度概述（只供模型推导穿搭/出行，不向用户播报数字）。
_WMO = {
    0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
    61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "rain", 81: "rain", 82: "rain", 85: "snow", 86: "snow",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}


def _fallback(cfg: dict) -> WeatherResult:
    """写死兜底（断网/定位失败/MOCK）：来源标 fallback/mock，绝不阻塞。"""
    w = cfg.get("weather", {}) or {}
    return WeatherResult(
        summary="clear",
        temp=float(w.get("fallback_temp", 22.0)),
        precip=0.0,
        source="mock" if is_mock("MOCK_WEATHER") else "fallback",
    )


async def weather_get(
    lat: Optional[float] = None, lon: Optional[float] = None, cfg: Optional[dict] = None
) -> WeatherResult:
    """查天气 → WeatherResult。lat/lon 缺省（定位失败）→ 回落默认城市坐标。

    MOCK_WEATHER=1 → 直接兜底；否则走 Open-Meteo（httpx，无 key）+ 坐标/小时缓存。
    任何失败（网络/超时/解析）都 catch → 回落兜底而非抛错——天气是「唯一牺牲位」，绝不阻塞主链路（PRD §5）。
    """
    if cfg is None:
        cfg = load_config()
    if is_mock("MOCK_WEATHER"):
        return _fallback(cfg)

    w = cfg.get("weather", {}) or {}
    if lat is None or lon is None:  # 定位失败/拒绝 → 静默回落默认城市坐标
        lat = w.get("fallback_lat")
        lon = w.get("fallback_lon")
    if lat is None or lon is None:  # 连兜底坐标都没有 → 写死兜底
        return _fallback(cfg)

    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.time()
    cached = _CACHE.get(key)
    if cached and cached[0] > now:  # 命中未过期缓存（按坐标 + TTL 小时）
        return cached[1]

    try:
        base = w.get("api_base") or "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,precipitation,weather_code,wind_speed_10m",
        }
        import httpx  # 延迟导入：MOCK/兜底路径零外部依赖（契约·MOCK）

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            resp = await client.get(base, params=params)
            resp.raise_for_status()
            cur = (resp.json() or {}).get("current") or {}
        wind = cur.get("wind_speed_10m")
        result = WeatherResult(
            summary=_WMO.get(int(cur.get("weather_code", -1)), "clear"),
            temp=float(cur.get("temperature_2m", 0.0)),
            precip=float(cur.get("precipitation", 0.0)),
            wind=float(wind) if wind is not None else None,
            source="open-meteo",
        )
        ttl_h = float(w.get("cache_ttl_hours", 1) or 0)
        _CACHE[key] = (now + ttl_h * 3600, result)
        return result
    except Exception as e:  # 网络/超时/解析任何失败 → 静默回落兜底，绝不阻塞（PRD §5）
        log.warning("weather_get 失败，回落兜底：%s", e)
        return _fallback(cfg)
