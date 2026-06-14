"""M4-01 · weather_get 真路径单测（Open-Meteo 解析 + 任何失败回落兜底 + MOCK + 缓存）。

不触真实网络：monkeypatch httpx.AsyncClient 注入伪造响应/异常。绝不阻塞、绝不抛——
任何失败都回落 _fallback（PRD §5 天气是「唯一牺牲位」）。
"""

import asyncio

import server.tools.weather as weather
from contracts.weather import WeatherResult

# Open-Meteo current 字段样例：weather_code=61 → rain。
_OPEN_METEO = {
    "current": {"temperature_2m": 18.5, "precipitation": 0.6, "weather_code": 61, "wind_speed_10m": 12.0}
}
_CFG = {
    "weather": {
        "api_base": "https://example.test/forecast",
        "fallback_lat": 31.23,
        "fallback_lon": 121.47,
        "cache_ttl_hours": 1,
    }
}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _Client:
    """伪造 httpx.AsyncClient：data 给定则返回；exc 给定则 get 抛错。"""

    def __init__(self, data=None, exc=None):
        self._data = data
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        if self._exc:
            raise self._exc
        return _Resp(self._data)


def _patch_httpx(monkeypatch, data=None, exc=None):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client(data, exc))


def test_weather_real_parse(monkeypatch):
    weather._CACHE.clear()
    monkeypatch.delenv("MOCK_WEATHER", raising=False)
    _patch_httpx(monkeypatch, data=_OPEN_METEO)
    r = asyncio.run(weather.weather_get(40.0, 116.0, _CFG))
    assert isinstance(r, WeatherResult)
    assert r.source == "open-meteo"
    assert r.summary == "rain"  # weather_code 61
    assert r.temp == 18.5 and r.precip == 0.6 and r.wind == 12.0


def test_weather_failure_falls_back(monkeypatch):
    weather._CACHE.clear()
    monkeypatch.delenv("MOCK_WEATHER", raising=False)
    _patch_httpx(monkeypatch, exc=RuntimeError("network down"))
    r = asyncio.run(weather.weather_get(40.1, 116.1, _CFG))
    assert r.source == "fallback"  # 任何失败 → 兜底，不抛


def test_weather_mock_env_no_network(monkeypatch):
    weather._CACHE.clear()
    monkeypatch.setenv("MOCK_WEATHER", "1")
    # MOCK 路径不触网（即便不 patch httpx 也能跑）→ source=mock。
    r = asyncio.run(weather.weather_get(40.0, 116.0, _CFG))
    assert r.source == "mock"


def test_weather_no_coords_falls_back(monkeypatch):
    weather._CACHE.clear()
    monkeypatch.delenv("MOCK_WEATHER", raising=False)
    # cfg 无 fallback 坐标 + 未传 lat/lon → 连兜底坐标都没有 → 写死兜底（不触网）。
    r = asyncio.run(weather.weather_get(None, None, {"weather": {}}))
    assert r.source == "fallback"


def test_weather_cache_hit(monkeypatch):
    weather._CACHE.clear()
    monkeypatch.delenv("MOCK_WEATHER", raising=False)
    _patch_httpx(monkeypatch, data=_OPEN_METEO)
    r1 = asyncio.run(weather.weather_get(50.0, 8.0, _CFG))
    assert r1.source == "open-meteo"
    # 第二次让 get 抛错；若命中缓存则仍返回 open-meteo（证明没再触网）。
    _patch_httpx(monkeypatch, exc=RuntimeError("should not be called"))
    r2 = asyncio.run(weather.weather_get(50.0, 8.0, _CFG))
    assert r2.source == "open-meteo" and r2.temp == r1.temp
