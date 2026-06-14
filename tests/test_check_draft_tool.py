"""check_draft 真帧批改（M3-01）：gemini 结构化输出硬约束（绿层，模型碰不到）。

只用伪造 _generate / 伪造 google.genai.types，零外部依赖、可独立运行：
    uv run pytest -q

验收点：
  - 三值 verdict 各能装成 CheckDraftResult（found_error 带 error_line/error_type）；
  - found_error 缺 error_line → 重试耗尽抛 RuntimeError（红线落在 vision_retry_max 覆盖内）；
  - check_draft 真分支传给 SDK 的 config 含 response_mime_type='application/json' / response_schema
    非 None / model 取自 cfg.roles.vision.model（非字面量）；
  - _CheckDraftExtract 的 verdict/error_type 枚举集合 == 契约 Verdict/ErrorType 取值（单一真源）。
"""

import asyncio
import json
import sys
import types as _pytypes
import typing

import pytest

from contracts.vision import CheckDraftResult, ErrorType, Verdict
from server.tools import vision_tools

# check_draft 真分支必须避开 MOCK 旁路：本测试全程真实路径（注入伪造 _generate / SDK）。
_CFG = {
    "roles": {"vision": {"provider": "gemini", "model": "gemini-2.5-flash-TESTONLY"}},
    "providers": {"gemini": {"api_key_env": "GEMINI_API_KEY"}},
    "session": {"vision_retry_max": 1},
}


def _run(coro):
    return asyncio.run(coro)


def _stub_generate(monkeypatch, payload: dict):
    """注入伪造 _generate：不连网，直接吐 payload 的 JSON 文本（走 _strip_json）。"""

    async def fake_generate(instruction, frame, cfg, schema=None):
        return json.dumps(payload)

    monkeypatch.setattr(vision_tools, "_generate", fake_generate)


# ── ① 三值各自能装成 CheckDraftResult ──
def test_found_error_packs(monkeypatch):
    _stub_generate(
        monkeypatch,
        {"verdict": "found_error", "error_line": 3, "error_type": "transpose_error", "confidence": 0.88},
    )
    res = _run(vision_tools.check_draft(b"jpeg", _CFG))
    assert isinstance(res, CheckDraftResult)
    assert res.verdict == Verdict.FOUND_ERROR
    assert res.error_line == 3
    assert res.error_type == ErrorType.TRANSPOSE_ERROR
    assert res.confidence == pytest.approx(0.88)


def test_all_correct_packs(monkeypatch):
    _stub_generate(monkeypatch, {"verdict": "all_correct", "confidence": 0.95})
    res = _run(vision_tools.check_draft(b"jpeg", _CFG))
    assert res.verdict == Verdict.ALL_CORRECT
    assert res.error_line is None
    assert res.error_type is None
    assert res.confidence == pytest.approx(0.95)


def test_unreadable_packs(monkeypatch):
    _stub_generate(monkeypatch, {"verdict": "unreadable", "confidence": 0.2})
    res = _run(vision_tools.check_draft(b"jpeg", _CFG))
    assert res.verdict == Verdict.UNREADABLE
    assert res.error_line is None
    # confidence 始终独立返回（与 verdict 无关）。
    assert res.confidence == pytest.approx(0.2)


# ── ② found_error 缺 error_line → 重试并最终 RuntimeError（不静默放过非法形态）──
def test_found_error_without_line_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    async def fake_generate(instruction, frame, cfg, schema=None):
        calls["n"] += 1
        return json.dumps({"verdict": "found_error", "confidence": 0.9})

    monkeypatch.setattr(vision_tools, "_generate", fake_generate)
    with pytest.raises(RuntimeError):
        _run(vision_tools.check_draft(b"jpeg", _CFG))
    # vision_retry_max=1 → 首发 + 1 次重试 = 2 次调用（红线 ValueError 落在重试覆盖内）。
    assert calls["n"] == _CFG["session"]["vision_retry_max"] + 1


# ── ③ 真分支传给 SDK 的 config 形态 + model 取自 config（非字面量）──
def test_generate_config_is_structured_and_model_from_config(monkeypatch):
    """注入伪造 google.genai.types + 伪造 client，断言 check_draft 走结构化输出 + config 模型名。"""
    captured = {}

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakePart:
        @staticmethod
        def from_bytes(data, mime_type):
            return ("part", mime_type)

    fake_types = _pytypes.SimpleNamespace(
        GenerateContentConfig=_FakeConfig, Part=_FakePart
    )
    fake_genai = _pytypes.ModuleType("google.genai")
    fake_genai.types = fake_types
    fake_google = _pytypes.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    class _FakeModels:
        async def generate_content(self, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            return _pytypes.SimpleNamespace(
                text=json.dumps({"verdict": "all_correct", "confidence": 0.9})
            )

    class _FakeClient:
        aio = _pytypes.SimpleNamespace(models=_FakeModels())

    monkeypatch.setattr(vision_tools, "client_for_role", lambda role, cfg: _FakeClient())

    res = _run(vision_tools.check_draft(b"jpeg", _CFG))
    assert res.verdict == Verdict.ALL_CORRECT

    cfg_obj = captured["config"]
    assert cfg_obj is not None
    assert cfg_obj.response_mime_type == "application/json"
    assert cfg_obj.response_schema is not None
    assert cfg_obj.response_schema is vision_tools._CheckDraftExtract
    assert cfg_obj.temperature == 0.0
    # 模型名取自 config.roles.vision.model，非代码字面量。
    assert captured["model"] == _CFG["roles"]["vision"]["model"]


# ── ④ _CheckDraftExtract 枚举 == 契约取值（单一真源）──
def test_extract_schema_enums_are_single_source():
    fields = vision_tools._CheckDraftExtract.model_fields
    verdict_enum = fields["verdict"].annotation
    assert verdict_enum is Verdict
    assert {m.value for m in verdict_enum} == {v.value for v in Verdict}

    # error_type 是 Optional[ErrorType]；从 Optional 里取出枚举，断言取值 == 契约单一真源。
    et_args = [a for a in typing.get_args(fields["error_type"].annotation) if a is not type(None)]
    assert et_args == [ErrorType]
    assert {m.value for m in et_args[0]} == {e.value for e in ErrorType}
    # schema 仅四字段，结构上无承载答案的字段。
    assert set(fields) == {"verdict", "error_line", "error_type", "confidence"}


# ── 回归：MOCK_VISION=1 三 fixture 仍过校验，且不触 google.genai ──
def test_mock_branch_still_passes(monkeypatch):
    monkeypatch.setenv("MOCK_VISION", "1")
    res = _run(vision_tools.check_draft(b"unused"))
    assert isinstance(res, CheckDraftResult)
    assert res.verdict in set(Verdict)
