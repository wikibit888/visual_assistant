"""契约自检（Live 版）：每条 fixture 必须能被对应契约模型校验（新 M0 验收）。

只依赖 pydantic（+ 标准库），无外部服务——验证契约层 + fixture 自洽、可独立运行：
    uv run pytest -q

两类 fixture：
  - 信封类（ENVELOPE_FILES）：客户端⇄后端 WS 控制帧，整条是 Envelope（契约一），按 type 再校 payload。
  - 工具结果类（TOOL_RESULT_FILES）：function_call/response 的内部 schema（不经客户端 WS），
    每条是裸 payload，直接对固定模型校验。
"""

import json
from pathlib import Path

import pytest

from contracts import (
    ConfigPushPayload,
    Envelope,
    ErrorEvent,
    FrameRequest,
    FrameResponse,
    Interrupted,
    LookAtPageResult,
    CheckDraftResult,
    ObserveResult,
    PostureAlert,
    SessionReady,
    SessionStart,
    SessionUpdate,
    TextInput,
    ToolActivity,
    Transcript,
    Verdict,
    WeatherGetArgs,
    WeatherResult,
    MessageType,
)

FIXTURES = Path(__file__).parent / "fixtures"

# type → payload 模型（信封类）。空载 type（payload={}）登记为 None：只校信封结构。
PAYLOAD_MODEL = {
    "session.start": SessionStart,
    "session.update": SessionUpdate,
    "session.ready": SessionReady,
    "input.activity_start": None,
    "input.activity_end": None,
    "interrupted": Interrupted,
    "transcript": Transcript,
    "tool.activity": ToolActivity,
    "frame.request": FrameRequest,
    "frame.response": FrameResponse,
    "config.push": ConfigPushPayload,
    "text.input": TextInput,
    "error": ErrorEvent,
    "posture.alert": PostureAlert,
}

ENVELOPE_FILES = [
    "session.jsonl",
    "audio.jsonl",
    "transcript.jsonl",
    "frame.jsonl",
    "control.jsonl",
    "posture_alert.jsonl",
]

# 工具结果类（裸 payload → 固定模型）。
TOOL_RESULT_FILES = {
    "vision_look_at_page.jsonl": LookAtPageResult,
    "vision_check_draft.jsonl": CheckDraftResult,
    "vision_observe.jsonl": ObserveResult,
    "weather_args.jsonl": WeatherGetArgs,
    "weather_result.jsonl": WeatherResult,
}


def _lines(name):
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _envelope_rows():
    for name in ENVELOPE_FILES:
        for row in _lines(name):
            yield name, row


def _tool_rows():
    for name, model in TOOL_RESULT_FILES.items():
        for row in _lines(name):
            yield name, model, row


@pytest.mark.parametrize("name,row", list(_envelope_rows()))
def test_envelope_and_payload_valid(name, row):
    """信封结构（契约一）+ payload（对应契约模型）双重校验。"""
    env = Envelope.model_validate(row)
    assert env.type.value in PAYLOAD_MODEL, f"{name}: 未登记 type={env.type.value} 的 payload 模型"
    model = PAYLOAD_MODEL[env.type.value]
    if model is None:  # 空载 type：只要求 payload 是（空）对象
        assert env.payload == {}, f"{name}: type={env.type.value} 应为空载 payload"
    else:
        model.model_validate(env.payload)


@pytest.mark.parametrize("name,model,row", list(_tool_rows()))
def test_tool_result_payload_valid(name, model, row):
    """工具 function_call/response 的内部 schema 校验（裸 payload）。"""
    model.model_validate(row)


def test_every_message_type_has_a_fixture():
    """全量 MessageType 都有 fixture 覆盖——新增 type 必须补样例（防协议漂移）。"""
    seen = {Envelope.model_validate(row).type for _, row in _envelope_rows()}
    missing = set(MessageType) - seen
    assert not missing, f"以下 MessageType 缺 fixture：{sorted(m.value for m in missing)}"


def test_three_verdicts_each_present():
    """契约八：verdict 三种取值（found_error/all_correct/unreadable）各至少一条样例。"""
    seen = {CheckDraftResult.model_validate(row).verdict for row in _lines("vision_check_draft.jsonl")}
    assert seen == set(Verdict), f"verdict 不齐：缺 {set(Verdict) - seen}"


def test_found_error_requires_error_line():
    """契约八红线：verdict=found_error 缺 error_line 必须被拒。"""
    with pytest.raises(Exception):
        CheckDraftResult.model_validate(
            {"kind": "check_draft", "verdict": "found_error", "confidence": 0.9}
        )


def test_posture_alert_reminder_count_optional():
    """契约七：reminder_count 可选（向后兼容）——缺省=None（后端落「又一次」），带时须 int ge=1。"""
    assert PostureAlert.model_validate({"severity": "hunchback", "ts": 1}).reminder_count is None
    assert (
        PostureAlert.model_validate({"severity": "hunchback", "ts": 1, "reminder_count": 3}).reminder_count
        == 3
    )
    with pytest.raises(Exception):  # ge=1：0 / 负数非法
        PostureAlert.model_validate({"severity": "hunchback", "ts": 1, "reminder_count": 0})


def test_session_start_geo_optional():
    """契约二：session.start 的 lat/lon 可选（生活模式定位）——缺省=None，带时成对 float。"""
    s0 = SessionStart.model_validate({"mode": "life", "voice_mode": "ptt", "subtitles": True})
    assert s0.lat is None and s0.lon is None
    s1 = SessionStart.model_validate({"mode": "life", "lat": 31.23, "lon": 121.47})
    assert s1.lat == 31.23 and s1.lon == 121.47
