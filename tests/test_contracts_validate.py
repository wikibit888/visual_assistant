"""契约自检：每条 fixture 都必须能被对应契约模型校验通过（M0 验收）。

只依赖 pydantic（+ 标准库），无外部服务——验证契约层 + fixture 自洽、可独立运行：
    pytest -q
覆盖：契约一信封 + 二语音 + 三视觉(四值 verdict) + 四间隙/姿态 + 八 planner 输出 + 十工作记忆。
"""

import json
from pathlib import Path

import pytest

from contracts import (
    AsrFinal,
    CheckDraftResult,
    Envelope,
    GapOpen,
    ObserveResult,
    PlannerOutput,
    PostureAlert,
    ReadProblemResult,
    TtsAck,
    TtsSay,
    TtsStop,
    Verdict,
    WeatherGetArgs,
    WeatherResult,
    WorkingMemory,
)

FIXTURES = Path(__file__).parent / "fixtures"

# type → payload 模型（信封类 fixture）。vision.result 按 payload.kind 再分。
PAYLOAD_MODEL = {
    "asr.final": AsrFinal,
    "tts.say": TtsSay,
    "tts.stop": TtsStop,
    "tts.ack": TtsAck,
    "weather.request": WeatherGetArgs,
    "weather.result": WeatherResult,
    "posture.alert": PostureAlert,
    "gap.open": GapOpen,
}
VISION_BY_KIND = {
    "read_problem": ReadProblemResult,
    "check_draft": CheckDraftResult,
    "observe": ObserveResult,
}

ENVELOPE_FILES = [
    "voice.jsonl",
    "vision_read_problem.jsonl",
    "vision_check_draft.jsonl",
    "vision_observe.jsonl",
    "weather.jsonl",
    "posture_alert.jsonl",
    "gap_open.jsonl",
]


def _lines(name):
    path = FIXTURES / name
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _envelope_rows():
    for name in ENVELOPE_FILES:
        for row in _lines(name):
            yield name, row


@pytest.mark.parametrize("name,row", list(_envelope_rows()))
def test_envelope_and_payload_valid(name, row):
    """信封结构（契约一）+ payload（对应契约模型）双重校验。"""
    env = Envelope.model_validate(row)
    if env.type.value == "vision.result":
        model = VISION_BY_KIND[env.payload["kind"]]
    else:
        model = PAYLOAD_MODEL.get(env.type.value)
    assert model is not None, f"{name}: 未登记 type={env.type.value} 的 payload 模型"
    model.model_validate(env.payload)


def test_four_verdicts_each_present():
    """契约三：四值 verdict 四种取值各至少一条样例。"""
    seen = set()
    for row in _lines("vision_check_draft.jsonl"):
        seen.add(CheckDraftResult.model_validate(row["payload"]).verdict)
    assert seen == set(Verdict), f"四值 verdict 不齐：缺 {set(Verdict) - seen}"


def test_planner_outputs_valid():
    """契约八：planner 结构化输出（answer/tool_calls/clarify）。"""
    rows = _lines("planner_output.jsonl")
    assert rows, "planner_output fixture 为空"
    for row in rows:
        PlannerOutput.model_validate(row)


def test_working_memory_valid():
    """契约十：工作记忆 schema。"""
    for row in _lines("working_memory.jsonl"):
        WorkingMemory.model_validate(row)


def test_found_error_requires_error_line():
    """契约三红线：verdict=found_error 缺 error_line 必须被拒。"""
    with pytest.raises(Exception):
        CheckDraftResult.model_validate(
            {"kind": "check_draft", "verdict": "found_error", "confidence": 0.9}
        )
