"""M2-04 验收：C 视觉 observe（MOCK_VISION=1 读 fixture）。

零外部服务/零 LLM：MOCK_VISION 口径下只读 tests/fixtures/vision_observe.jsonl，
返回须是合规契约三 ObserveResult，且 confidence 原样透传（交 A 护栏裁决，C 不自决）。
observe 是协程；无 pytest-asyncio，用 asyncio.run 驱动。
"""

import asyncio
import json
from pathlib import Path

import pytest

from contracts.vision import ObserveResult, VisionKind
from server.c_vision import vision_tools

FIXTURE = Path(__file__).parent / "fixtures" / "vision_observe.jsonl"


def _first_fixture_payload():
    """MOCK observe 读首行——测试与实现口径一致（确定性）。"""
    with FIXTURE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return json.loads(line)["payload"]
    raise AssertionError("fixture 为空")


@pytest.fixture
def mock_vision(monkeypatch):
    monkeypatch.setenv("MOCK_VISION", "1")


def test_observe_mock_returns_valid_result(mock_vision):
    """(a) MOCK_VISION=1 → 返回合规 ObserveResult（契约三 kind=observe）。"""
    out = asyncio.run(vision_tools.observe())
    assert isinstance(out, ObserveResult)
    ObserveResult.model_validate(out.model_dump())         # 构造 → 序列化 → 再校验
    assert out.kind == VisionKind.OBSERVE
    assert out.description.strip()                          # 描述非空
    assert 0.0 <= out.confidence <= 1.0


def test_observe_mock_matches_first_fixture(mock_vision):
    """MOCK 读首行：description/confidence 与 fixture 首条 payload 一致（确定性）。"""
    payload = _first_fixture_payload()
    out = asyncio.run(vision_tools.observe())
    assert out.description == payload["description"]
    # confidence 原样透传，不被 C 改写/夹取（PRD §7.4：是否播报由 A 护栏裁）。
    assert out.confidence == payload["confidence"]


def test_observe_mock_with_hint_ok(mock_vision):
    """(b) 传 hint（如 "outfit"）不崩，仍返回合规结果。"""
    out = asyncio.run(vision_tools.observe(hint="outfit"))
    assert isinstance(out, ObserveResult)
    assert out.kind == VisionKind.OBSERVE
    assert out.description.strip()
    assert 0.0 <= out.confidence <= 1.0


def test_observe_mock_ignores_cfg(mock_vision):
    """MOCK 路径脱依赖：传入 cfg 也不触真实客户端，仍读 fixture。"""
    out = asyncio.run(vision_tools.observe(hint="杯子", cfg={"roles": {}}))
    assert isinstance(out, ObserveResult)
    assert out.confidence == _first_fixture_payload()["confidence"]


def test_observe_mock_no_network(mock_vision, monkeypatch):
    """(c) MOCK 路径不构造真实客户端、不发任何网络——client_for_role 被调用即失败。"""
    def _boom(*args, **kwargs):
        raise AssertionError("MOCK_VISION 路径不得触达 client_for_role（应零外部依赖）")

    monkeypatch.setattr(vision_tools, "client_for_role", _boom)
    out = asyncio.run(vision_tools.observe(hint="outfit"))
    assert isinstance(out, ObserveResult)
    assert out.kind == VisionKind.OBSERVE
