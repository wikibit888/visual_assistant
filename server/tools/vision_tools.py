"""工具 · 视觉识别执行体（契约·视觉；PRD §3 / §5）。

三个 function_call 的执行体。入参 `frame` = 客户端经 frame.response 回传的当前帧（JPEG 字节）。
  - look_at_page(frame, cfg) → LookAtPageResult{text, confidence}      识题/读草稿原文，不解题
  - check_draft(frame, cfg)  → CheckDraftResult{verdict, error_line?, confidence}  批改，只定位错误行
  - observe(frame, hint, cfg)→ ObserveResult{description, confidence}  一句客观描述

真实路径：client_for_role("vision", cfg) → google.genai 多模态；模型名 per-call 取自
config.roles.vision（禁硬编码——契约·配置）。失败按 config.session.vision_retry_max 重试。
MOCK_VISION=1：读 tests/fixtures/vision_*.jsonl 首条（脱依赖、可独立运行——契约·MOCK）。
confidence 原样返回——是否/如何向用户说由 Live 模型 + 提示词裁，工具不自决（PRD §5）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from contracts.config_schema import load_config
from contracts.mock import is_mock
from contracts.vision import CheckDraftResult, LookAtPageResult, ObserveResult
from server.llm.providers import client_for_role

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIX = _REPO_ROOT / "tests" / "fixtures"

# 「结构化抽取指令」——非技能/人设 prompt（那归 server/skills）；仅约束 gemini 产出契约 JSON 形态。
_LOOK_INSTRUCTION = (
    "你是纸面识别器。原样转写画面中用户指向的题目或草稿文本，不解题、不补全、不翻译。"
    "同时给出识别完整性的置信度 confidence（0~1）。"
    '只输出 JSON：{"text": "...", "confidence": 0.0}'
)
_CHECK_INSTRUCTION = (
    "你是批改器。看用户写的草稿，只定位第一处错误的「行号 + 错误类型」，不报正确答案。"
    "verdict 取值：found_error（定位到错误，必须给 1-based error_line）/ all_correct（全对）/ "
    "unreadable（看不清，不要编造）。error_type 可选：sign_error/transpose_error/calc_error/other。"
    "同时给出 confidence（0~1）。"
    '只输出 JSON：{"verdict": "...", "error_line": 0, "error_type": "...", "confidence": 0.0}'
)
_OBSERVE_INSTRUCTION = (
    "你是画面观察器。用一句话客观描述画面中的穿搭或随手物体，只陈述所见、不评价、不建议。"
    "同时给出描述可靠性的置信度 confidence（0~1）。"
    '只输出 JSON：{"description": "...", "confidence": 0.0}'
)


def _first_fixture_payload(name: str) -> dict:
    with (_FIX / name).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return json.loads(line)
    raise RuntimeError(f"MOCK_VISION fixture 为空：{name}")


def _strip_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):  # 容忍 ```json fenced``` 包裹
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return json.loads(raw)


async def _generate(instruction: str, frame: bytes, cfg: dict) -> str:
    """调 gemini 多模态客户端 → 原始文本。model per-call 取自 config.roles.vision（禁硬编码）。"""
    client = client_for_role("vision", cfg)
    model = cfg["roles"]["vision"]["model"]
    from google.genai import types  # 延迟导入：MOCK 路径零依赖

    parts = [instruction, types.Part.from_bytes(data=frame, mime_type="image/jpeg")]
    resp = await client.aio.models.generate_content(model=model, contents=parts)
    return resp.text


async def _run(instruction: str, frame: bytes, cfg: Optional[dict]):
    """真实路径公共骨架：取 cfg + 按 vision_retry_max 重试 → 返回解析后的 dict。"""
    if cfg is None:
        cfg = load_config()
    retry_max = int(cfg.get("session", {}).get("vision_retry_max", 0))
    last_err: Optional[Exception] = None
    for _ in range(retry_max + 1):
        try:
            return _strip_json(await _generate(instruction, frame, cfg))
        except Exception as e:  # 网络/解析失败 → 受 config 重试上限约束重试
            last_err = e
    raise RuntimeError("vision 工具失败（已达 vision_retry_max）") from last_err


async def look_at_page(frame: bytes, cfg: Optional[dict] = None) -> LookAtPageResult:
    """识题/读草稿（契约·视觉 kind=look_at_page）→ LookAtPageResult{text, confidence}。"""
    if is_mock("MOCK_VISION"):
        return LookAtPageResult.model_validate(_first_fixture_payload("vision_look_at_page.jsonl"))
    data = await _run(_LOOK_INSTRUCTION, frame, cfg)
    return LookAtPageResult(text=data["text"], confidence=data["confidence"])


async def check_draft(frame: bytes, cfg: Optional[dict] = None) -> CheckDraftResult:
    """批改（契约·视觉 kind=check_draft）→ CheckDraftResult{verdict, error_line?, confidence}。"""
    if is_mock("MOCK_VISION"):
        return CheckDraftResult.model_validate(_first_fixture_payload("vision_check_draft.jsonl"))
    data = await _run(_CHECK_INSTRUCTION, frame, cfg)
    return CheckDraftResult.model_validate(
        {
            "verdict": data["verdict"],
            "error_line": data.get("error_line") or None,
            "error_type": data.get("error_type") or None,
            "confidence": data["confidence"],
        }
    )


async def observe(
    frame: bytes, hint: Optional[str] = None, cfg: Optional[dict] = None
) -> ObserveResult:
    """穿搭/即兴物体（契约·视觉 kind=observe）→ ObserveResult{description, confidence}。"""
    if is_mock("MOCK_VISION"):
        return ObserveResult.model_validate(_first_fixture_payload("vision_observe.jsonl"))
    instruction = _OBSERVE_INSTRUCTION if not hint else f"{_OBSERVE_INSTRUCTION}\n关注：{hint}"
    data = await _run(instruction, frame, cfg)
    return ObserveResult(description=data["description"], confidence=data["confidence"])
