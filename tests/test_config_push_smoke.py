"""冒烟：MOCK 全开下，WS 建连即收到 config.push，cfg 非 null（M1 config 下发）。

不依赖任何供应商（config.push 取自 load_config，与 MOCK 无关）；用 Starlette TestClient
跑真实 /ws 端点，验「建连 → 后端下发 config.push → cfg 可用」时序与 cfg 接口契约。
"""

import os

import pytest

from contracts import ConfigPushPayload

# 关键依赖缺失（如未装 fastapi/httpx）时跳过冒烟，不拖垮契约层验收。
fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture()
def mock_all(monkeypatch):
    """MOCK 全开：脱一切供应商依赖（契约六）。"""
    for key in (
        "MOCK_VISION",
        "MOCK_WEATHER",
        "MOCK_ASR",
        "MOCK_TTS",
        "MOCK_PLANNER",
        "MOCK_LLM",
    ):
        monkeypatch.setenv(key, "1")


def test_ws_pushes_config_on_connect(mock_all):
    """建连后第一条下行即合规 config.push：control 通道、t-000000、cfg 非 null。"""
    from server.main import create_app

    app = create_app()
    client = fastapi_testclient.TestClient(app)
    with client.websocket_connect("/ws") as ws:
        env = ws.receive_json()

    assert env["type"] == "config.push"
    assert env["channel"] == "control"
    assert env["turn_id"] == "t-000000"

    # payload 合规 + cfg 接口契约关键路径非 null（B/D init 依赖）。
    cfg = ConfigPushPayload.model_validate(env["payload"])
    assert cfg.turn_state.get("vad_speaking_min_ms") is not None
    assert cfg.posture.get("hunchback_hold_ms") is not None
