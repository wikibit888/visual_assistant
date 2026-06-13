"""M1-06b 冒烟：/ws 二进制音频帧 → asr.final 上总线（不破坏现有文本信封收发）。

MOCK_ASR=1：后端收音频 → asr_adapter 出固定文本 → 装配成 Envelope(asr.final, voice)
经现有 ws_router 占位回环 handler 上行回送。验「二进制帧分流 + asr.final 走信封」全链路。
另验文本信封（config.push 等）原有收发不受帧分流改造影响。
"""

import os

import pytest

from contracts import AsrFinal
from server.b_voice import asr_adapter

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture()
def mock_asr(monkeypatch):
    """开 MOCK_ASR：后端 ASR 脱供应商出固定文本（契约六）。"""
    monkeypatch.setenv("MOCK_ASR", "1")


def test_binary_audio_frame_yields_asr_final(mock_asr):
    """发二进制音频帧 → 收到合规 asr.final（voice 通道、t-NNNNNN、AsrFinal payload）。"""
    from server.main import create_app

    app = create_app()
    client = fastapi_testclient.TestClient(app)
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # 建连即下发的 config.push（先消费掉）
        ws.send_bytes(b"<fake-utterance-audio-bytes>")
        env = ws.receive_json()  # 占位 handler 回环上来的 asr.final

    assert env["type"] == "asr.final"
    assert env["channel"] == "voice"
    # turn_id 占位格式 t-NNNNNN（M1 装配层递增；M2 由 A 分配）。
    assert env["turn_id"].startswith("t-") and len(env["turn_id"]) == 8

    # payload 合规 AsrFinal（契约二）；MOCK 固定文本，与 audio 内容无关。
    final = AsrFinal.model_validate(env["payload"])
    assert final.text == asr_adapter.MOCK_TEXT
    assert final.confidence == asr_adapter.MOCK_CONFIDENCE
    assert final.turn_id == env["turn_id"]


def test_binary_turn_ids_increment_within_connection(mock_asr):
    """同连接内多段音频 → turn_id 占位计数递增（不重复）。"""
    from server.main import create_app

    app = create_app()
    client = fastapi_testclient.TestClient(app)
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # config.push
        ws.send_bytes(b"first")
        first = ws.receive_json()
        ws.send_bytes(b"second")
        second = ws.receive_json()

    assert first["type"] == second["type"] == "asr.final"
    assert first["turn_id"] != second["turn_id"]


def test_text_envelope_still_routes(mock_asr):
    """文本信封路径不受帧分流改造影响：发 asr.final 文本帧 → 占位 handler 原样回环。"""
    import time

    from contracts import Channel, Envelope, MessageType
    from server.main import create_app

    app = create_app()
    client = fastapi_testclient.TestClient(app)
    sent = Envelope(
        type=MessageType.ASR_FINAL,
        ts=int(time.time() * 1000),
        turn_id="t-000042",
        channel=Channel.VOICE,
        payload=AsrFinal(text="文本帧测试", confidence=0.9, turn_id="t-000042").model_dump(),
    )
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # config.push
        ws.send_text(sent.model_dump_json())
        echoed = ws.receive_json()

    assert echoed["type"] == "asr.final"
    assert echoed["turn_id"] == "t-000042"
    assert echoed["payload"]["text"] == "文本帧测试"
