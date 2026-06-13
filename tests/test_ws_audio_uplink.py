"""/ws 二进制音频帧 → A 编排 loop → tts.say 下行（不破坏现有文本信封收发）。

M2 活体接线后：后端收音频 → asr_adapter 出固定文本 → A 的 Session.handle_asr_final 跑
run_turn（编排 loop + 确定性护栏）→ 产 Envelope(tts.say, voice) 下行。本测验「二进制帧
分流 + 音频回合产 tts.say（不再回环 asr.final）+ turn_id 由 A 连内递增」全链路。
另验文本信封（config.push / ws_router 文本路径）原有收发不受帧分流 / 接线改造影响。

MOCK_ASR=1（固定识别文本）；二进制路径另开 MOCK_PLANNER + MOCK_VISION=1（编排走固定脚本、
工具读 fixture，零 LLM/零网络）——MOCK 文本含「题」会触发 read_problem 视觉工具回合。
"""

import pytest

from contracts import AsrFinal, TtsSay

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture()
def mock_asr(monkeypatch):
    """开 MOCK_ASR：后端 ASR 脱供应商出固定文本（契约六）。"""
    monkeypatch.setenv("MOCK_ASR", "1")


@pytest.fixture()
def mock_voice_loop(monkeypatch):
    """开 MOCK_ASR + MOCK_PLANNER + MOCK_VISION：ASR 固定文本 + 编排固定脚本 + 视觉读 fixture。

    MOCK 文本「这道题我不会，你看看」含「题」→ 脚本走 tool_calls(read_problem)，故须开
    MOCK_VISION 让 read_problem 工具读 fixture（契约六，全离线零网络）。
    """
    monkeypatch.setenv("MOCK_ASR", "1")
    monkeypatch.setenv("MOCK_PLANNER", "1")
    monkeypatch.setenv("MOCK_VISION", "1")


def test_binary_audio_frame_yields_tts_say(mock_voice_loop):
    """发二进制音频帧 → 经 A 编排 loop 产合规 tts.say（voice 通道、t-NNNNNN、TtsSay payload）。"""
    from server.main import create_app

    app = create_app()
    client = fastapi_testclient.TestClient(app)
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # 建连即下发的 config.push（先消费掉）
        ws.send_bytes(b"<fake-utterance-audio-bytes>")
        env = ws.receive_json()  # A 编排 loop 经护栏闸门下行的 tts.say

    # 音频回合现在产 tts.say（不再回环 asr.final）：A 只发 tts.say 给 B（铁律2）。
    assert env["type"] == "tts.say"
    assert env["channel"] == "voice"
    # turn_id 由 A 的 Session 分配，格式 t-NNNNNN。
    assert env["turn_id"].startswith("t-") and len(env["turn_id"]) == 8

    # payload 合规 TtsSay（契约二）；候选文本经护栏闸门收口，非空。
    say = TtsSay.model_validate(env["payload"])
    assert say.text.strip()
    assert say.turn_id == env["turn_id"]


def test_binary_turn_ids_increment_within_connection(mock_voice_loop):
    """同连接内多段音频 → turn_id 由 A 连内递增（不重复），仍产 tts.say。"""
    from server.main import create_app

    app = create_app()
    client = fastapi_testclient.TestClient(app)
    with client.websocket_connect("/ws") as ws:
        _ = ws.receive_json()  # config.push
        ws.send_bytes(b"first")
        first = ws.receive_json()
        ws.send_bytes(b"second")
        second = ws.receive_json()

    assert first["type"] == second["type"] == "tts.say"
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
