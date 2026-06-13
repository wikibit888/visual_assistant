"""M1-08 验收：C 视觉 read_problem（MOCK_VISION=1 读 fixture）。

零外部服务/零 LLM：MOCK_VISION 口径下只读 tests/fixtures/vision_read_problem.jsonl，
返回须是合规契约三 ReadProblemResult，且 confidence 原样透传（交 A 护栏裁决，C 不自决）。
read_problem 是协程；无 pytest-asyncio，用 asyncio.run 驱动。
"""

import asyncio
import json
from pathlib import Path

import pytest

from contracts.vision import ReadProblemResult, VisionKind
from server.c_vision import vision_tools

FIXTURE = Path(__file__).parent / "fixtures" / "vision_read_problem.jsonl"


def _fixture_payload():
    with FIXTURE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return json.loads(line)["payload"]
    raise AssertionError("fixture 为空")


@pytest.fixture
def mock_vision(monkeypatch):
    monkeypatch.setenv("MOCK_VISION", "1")


def test_read_problem_mock_returns_valid_result(mock_vision):
    """MOCK_VISION=1 → 返回合规 ReadProblemResult（契约三 kind=read_problem）。"""
    out = asyncio.run(vision_tools.read_problem())
    assert isinstance(out, ReadProblemResult)
    ReadProblemResult.model_validate(out.model_dump())     # 构造 → 序列化 → 再校验
    assert out.kind == VisionKind.READ_PROBLEM
    assert out.problem_text.strip()                        # 题面非空
    assert 0.0 <= out.confidence <= 1.0


def test_read_problem_mock_matches_fixture(mock_vision):
    """读的就是 fixture 那条——problem_text/confidence 与 fixture payload 一致。"""
    payload = _fixture_payload()
    out = asyncio.run(vision_tools.read_problem())
    assert out.problem_text == payload["problem_text"]
    # confidence 原样透传，不被 C 改写/夹取（PRD §7.4：是否播报由 A 护栏裁）。
    assert out.confidence == payload["confidence"]


def test_read_problem_mock_ignores_frame_and_cfg(mock_vision):
    """MOCK 路径脱依赖：传入 frame/cfg 也不触真实客户端，仍读 fixture。"""
    out = asyncio.run(vision_tools.read_problem(frame=object(), cfg={"roles": {}}))
    assert isinstance(out, ReadProblemResult)
    assert out.confidence == _fixture_payload()["confidence"]
