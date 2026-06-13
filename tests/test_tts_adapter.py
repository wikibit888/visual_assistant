"""M1-07 验收：后端按句 TTS 适配器 + stop 语义（契约二 TtsSay/TtsStop/TtsAck）。

零外部服务：MOCK_TTS=1 产静音桩、脱供应商独立跑（契约六）。覆盖：
- 按句切分：多句 tts.say → 多帧；seq 自基准自增，**首句 seq 最小、先播**；turn_id 透传；
- stop = 立即停（进行中的 synthesize 在句界停产）+ 清播放队列 + 返回 contracts.TtsAck(stopped=True)；
- MOCK 路径不触供应商（不调 client_for_role）——真·脱依赖；
- 真实路径经 server.llm.providers.client_for_role("tts", cfg) 解析（禁硬编码 provider/model），
  不旁路、不伪造音频帧。
无 pytest-asyncio：用 asyncio.run 驱动协程（与仓库现有零额外依赖口径一致）。
"""

import asyncio

import pytest

from contracts import TtsAck, TtsSay
from server.b_voice import tts_adapter
from server.b_voice.tts_adapter import TtsAdapter, TtsFrame


def _run(coro):
    return asyncio.run(coro)


async def _drain(say, cfg=None):
    """跑完无状态 synthesize，收集全部帧。"""
    frames = []
    async for f in tts_adapter.synthesize(say, cfg=cfg):
        frames.append(f)
    return frames


@pytest.fixture()
def mock_tts(monkeypatch):
    """开 MOCK_TTS：脱供应商依赖（契约六）。"""
    monkeypatch.setenv("MOCK_TTS", "1")


# ── 句切分纯函数 ──────────────────────────────────────────────────────

def test_split_sentences_keeps_punctuation_and_drops_blank():
    out = tts_adapter.split_sentences("你好。今天天气不错！要出门吗？")
    assert out == ["你好。", "今天天气不错！", "要出门吗？"]
    assert tts_adapter.split_sentences("") == []
    assert tts_adapter.split_sentences("   ") == []


def test_split_sentences_no_terminal_punct_is_one_sentence():
    assert tts_adapter.split_sentences("没有标点的一句话") == ["没有标点的一句话"]


# ── 按句切分 + 首句先播（seq 自增）─────────────────────────────────────

def test_mock_splits_into_sentences_seq_ascends_first_plays_first(mock_tts):
    say = TtsSay(text="第一句。第二句！第三句？", turn_id="t-000123", seq=0)
    frames = _run(_drain(say))

    assert len(frames) == 3
    assert all(isinstance(f, TtsFrame) for f in frames)
    # seq 自增、严格递增；首句 seq 最小（=基准 say.seq）。
    seqs = [f.seq for f in frames]
    assert seqs == [0, 1, 2]
    assert seqs[0] == min(seqs)
    # 产出顺序即播放顺序：首句（seq 最小）先 yield。
    assert frames[0].text == "第一句。"
    assert frames[-1].text == "第三句？"
    # turn_id 全程透传。
    assert all(f.turn_id == "t-000123" for f in frames)
    # MOCK 下音频为静音桩（非空、确定）。
    assert all(f.audio == tts_adapter.MOCK_SILENCE_FRAME for f in frames)


def test_mock_seq_offsets_from_say_seq_base(mock_tts):
    # say.seq 作基准：A 给定起始 seq 时，帧 seq 应自该基准自增（同回合按句续号）。
    say = TtsSay(text="甲。乙。", turn_id="t-000200", seq=5)
    frames = _run(_drain(say))
    assert [f.seq for f in frames] == [5, 6]


def test_mock_empty_text_yields_no_frames(mock_tts):
    say = TtsSay(text="   ", turn_id="t-000300", seq=0)
    assert _run(_drain(say)) == []


def test_mock_is_dependency_free_no_provider_call(mock_tts, monkeypatch):
    # 脱依赖（契约六）：MOCK 路径绝不触供应商工厂。
    def _boom(*args, **kwargs):
        raise AssertionError("MOCK_TTS 路径不应调用 client_for_role")

    monkeypatch.setattr("server.llm.providers.client_for_role", _boom)
    say = TtsSay(text="一句话。", turn_id="t-000007", seq=0)
    frames = _run(_drain(say))
    assert frames and frames[0].turn_id == "t-000007"


# ── stop 语义：立即停 + 清队列 + 回 TtsAck ─────────────────────────────

def test_stop_returns_tts_ack(mock_tts):
    adapter = TtsAdapter()
    ack = adapter.stop("t-000123")
    assert isinstance(ack, TtsAck)
    TtsAck.model_validate(ack.model_dump())            # 构造 → 序列化 → 再校验
    assert ack.turn_id == "t-000123"
    assert ack.stopped is True


def test_stop_clears_pending_queue(mock_tts):
    adapter = TtsAdapter()

    async def _scenario():
        say = TtsSay(text="甲。乙。丙。", turn_id="t-000123", seq=0)
        produced = []
        async for f in adapter.synthesize(say):
            produced.append(f)
        # 全部产出后队列含全部待播帧。
        assert len(adapter.pending) == 3
        ack = adapter.stop("t-000123")
        # stop = 清播放队列 + 回 ack。
        assert adapter.pending == []
        assert ack.stopped is True

    _run(_scenario())


def test_stop_mid_stream_halts_production_and_clears(mock_tts):
    # 立即停：synthesize 进行中调 stop，应在下一句界停产，且队列被清空。
    adapter = TtsAdapter()

    async def _scenario():
        say = TtsSay(text="甲。乙。丙。丁。戊。", turn_id="t-000123", seq=0)
        produced = []
        async for f in adapter.synthesize(say):
            produced.append(f)
            if len(produced) == 2:
                ack = adapter.stop("t-000123")
                assert ack.stopped is True
        # 停在第 2 句后：只产出 2 帧（未产完 5 句）。
        assert [f.text for f in produced] == ["甲。", "乙。"]
        # 队列已被 stop 清空。
        assert adapter.pending == []

    _run(_scenario())


# ── 真实路径：经 client_for_role 解析（禁硬编码 provider/model）──────────

def test_real_path_goes_through_client_for_role(monkeypatch):
    monkeypatch.delenv("MOCK_TTS", raising=False)
    captured = {}

    def _fake_client_for_role(role, cfg):
        captured["role"] = role
        captured["cfg"] = cfg
        return object()                                # 桩客户端，避免触真实 SDK

    monkeypatch.setattr("server.llm.providers.client_for_role", _fake_client_for_role)
    fake_cfg = {"roles": {"tts": {"provider": "gemini", "model": "stub-tts"}}}

    # 真实路径尚未接具体 TTS 协议 → NotImplementedError；关键是已走过 client_for_role。
    with pytest.raises(NotImplementedError):
        _run(_drain(TtsSay(text="一句话。", turn_id="t-000009", seq=0), cfg=fake_cfg))
    assert captured["role"] == "tts"                   # 角色绑定取自 config.roles.tts
    assert captured["cfg"] is fake_cfg                 # cfg 透传，未硬编码绕过


def test_real_path_empty_text_short_circuits_no_provider(monkeypatch):
    # 空文本无句可合成：早返回，不应触供应商（无须解析 config）。
    monkeypatch.delenv("MOCK_TTS", raising=False)

    def _boom(*args, **kwargs):
        raise AssertionError("空文本不应触发 client_for_role")

    monkeypatch.setattr("server.llm.providers.client_for_role", _boom)
    assert _run(_drain(TtsSay(text="", turn_id="t-000400", seq=0))) == []
