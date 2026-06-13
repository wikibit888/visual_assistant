"""M1-06 验收：后端 ASR 适配器 → asr.final（契约二 AsrFinal）。

零外部服务：MOCK_ASR=1 出固定文本 + confidence，脱供应商独立跑（契约六）。覆盖：
- MOCK 路径产出合规 AsrFinal（text/confidence/turn_id），turn_id 透传、文本恒定不随输入变；
- MOCK 路径不触供应商（不调 client_for_role）——真·脱依赖；
- 真实路径经 server.llm.providers.client_for_role("asr", cfg) 解析（禁硬编码 provider/model），
  不旁路、不伪造 AsrFinal。
无 pytest-asyncio：用 asyncio.run 驱动协程（与仓库现有零额外依赖口径一致）。
"""

import asyncio

import pytest

from contracts import AsrFinal
from server.b_voice import asr_adapter


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def mock_asr(monkeypatch):
    """开 MOCK_ASR：脱供应商依赖（契约六）。"""
    monkeypatch.setenv("MOCK_ASR", "1")


def test_mock_returns_schema_valid_asr_final(mock_asr):
    out = _run(asr_adapter.transcribe(b"<audio-bytes>", "t-000123"))
    assert isinstance(out, AsrFinal)
    AsrFinal.model_validate(out.model_dump())            # 构造 → 序列化 → 再校验
    assert out.turn_id == "t-000123"                     # turn_id 透传
    assert out.text and out.text.strip()                 # 固定文本非空
    assert 0.0 <= out.confidence <= 1.0                  # 契约二 confidence ∈ [0,1]


def test_mock_text_and_confidence_are_fixed(mock_asr):
    # MOCK 即桩：不做识别 → 文本/置信不随输入音频变，且确定可复现。
    a = _run(asr_adapter.transcribe(b"one", "t-000001"))
    b = _run(asr_adapter.transcribe(b"two-different-audio", "t-000002"))
    assert a.text == b.text == asr_adapter.MOCK_TEXT
    assert a.confidence == b.confidence == asr_adapter.MOCK_CONFIDENCE


def test_mock_is_dependency_free_no_provider_call(mock_asr, monkeypatch):
    # 脱依赖（契约六）：MOCK 路径绝不触供应商工厂。
    def _boom(*args, **kwargs):
        raise AssertionError("MOCK_ASR 路径不应调用 client_for_role")

    monkeypatch.setattr("server.llm.providers.client_for_role", _boom)
    out = _run(asr_adapter.transcribe(b"<audio-bytes>", "t-000007"))
    assert out.turn_id == "t-000007"


def test_real_path_goes_through_client_for_role(monkeypatch):
    # 非 MOCK：必须经 client_for_role("asr", cfg) 解析供应商（禁硬编码），不旁路/不伪造 AsrFinal。
    monkeypatch.delenv("MOCK_ASR", raising=False)
    captured = {}

    def _fake_client_for_role(role, cfg):
        captured["role"] = role
        captured["cfg"] = cfg
        return object()                                  # 桩客户端，避免触真实 SDK

    monkeypatch.setattr("server.llm.providers.client_for_role", _fake_client_for_role)
    fake_cfg = {"roles": {"asr": {"provider": "gemini", "model": "stub-stt"}}}

    # 真实路径尚未接具体 STT 协议 → NotImplementedError；关键是已走过 client_for_role。
    with pytest.raises(NotImplementedError):
        _run(asr_adapter.transcribe(b"<audio-bytes>", "t-000009", cfg=fake_cfg))
    assert captured["role"] == "asr"                     # 角色绑定取自 config.roles.asr
    assert captured["cfg"] is fake_cfg                   # cfg 透传，未硬编码绕过
