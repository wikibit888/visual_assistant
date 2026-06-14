"""工具 · weather_get 执行体（契约·天气；PRD §3 / §5）。

定位 → Open-Meteo（无 key）→ 城市/小时缓存 → 写死兜底。失败/超时/定位拒绝 → 静默回落
config.weather.fallback_city，绝不阻塞（PRD §5）。MOCK_WEATHER=1 直接返回兜底。

新 M0 骨架：MOCK / 兜底路径就绪（确定性、可独立跑）；真实 Open-Meteo HTTP 调用留 M-生活接。
不向用户播报数字——只供 Live 模型推导穿搭/出行的「具体动作」建议。
"""

from __future__ import annotations

from typing import Optional

from contracts.config_schema import load_config
from contracts.mock import is_mock
from contracts.weather import WeatherResult


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

    MOCK_WEATHER=1 → 直接兜底；否则走 Open-Meteo（真实 HTTP 调用留后续里程碑）。
    任何失败都回落兜底而非抛错——天气是「唯一牺牲位」，绝不阻塞主链路（PRD §5）。
    """
    if cfg is None:
        cfg = load_config()
    if is_mock("MOCK_WEATHER"):
        return _fallback(cfg)

    w = cfg.get("weather", {}) or {}
    if lat is None or lon is None:  # 定位失败/拒绝 → 静默回落默认城市坐标
        lat = w.get("fallback_lat")
        lon = w.get("fallback_lon")

    raise NotImplementedError(
        "M-生活：Open-Meteo HTTP 调用（httpx GET config.weather.api_base，lat/lon → "
        "{temp,precip}）+ 城市/小时缓存；任何失败 catch → _fallback(cfg)，绝不阻塞"
    )
