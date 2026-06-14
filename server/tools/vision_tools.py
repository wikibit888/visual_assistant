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

from pydantic import BaseModel, Field

from contracts.config_schema import load_config
from contracts.mock import is_mock
from contracts.vision import CheckDraftResult, ErrorType, LookAtPageResult, ObserveResult, Verdict
from server.llm.providers import client_for_role

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIX = _REPO_ROOT / "tests" / "fixtures"

# 视觉识别确定性常量（绿层；非阈值/非模型名，故不入 config——契约·配置）。
_VISION_TEMPERATURE = 0.0


class _CheckDraftExtract(BaseModel):
    """check_draft 的 gemini 结构化输出 schema（绿层硬约束，模型碰不到）。

    仅四字段，**结构上无处安放答案**：没有承载「正确答案/正确算式/正确解/改写式」的字段。
    枚举单一真源直接复用契约的 Verdict / ErrorType（取值 == [x.value for x in 契约枚举]，不手抄）。
    刻意不带契约 CheckDraftResult 的 `kind`（工具回填）与 found_error→error_line 校验器
    （SDK 不吃带 model_validator 的模型；红线在 check_draft 末尾 CheckDraftResult.model_validate 兜）。
    """

    verdict: Verdict
    error_line: Optional[int] = Field(None, ge=1)
    error_type: Optional[ErrorType] = None
    confidence: float = Field(..., ge=0.0, le=1.0)


# 「结构化抽取指令」——非技能/人设 prompt（那归 server/skills）；仅约束 gemini 产出契约 JSON 形态。
_LOOK_INSTRUCTION = (
    "你是纸面识别器。原样转写画面中用户指向的题目或草稿文本，不解题、不补全、不翻译。"
    "同时给出识别完整性的置信度 confidence（0~1）。"
    '只输出 JSON：{"text": "...", "confidence": 0.0}'
)
_CHECK_INSTRUCTION = (
    "你是批改器。看用户写的草稿，只定位第一处错误的「行号（1-based）+ 错误类型」。"
    "绝不写出、绝不暗示正确答案、正确算式或改正后的式子——只指出哪一行错、错在哪类。"
    "verdict：found_error（定位到错误，必须给 error_line）/ all_correct（全对）/ "
    "unreadable（看不清，不要猜、不要编造）。"
    "confidence 是你对「识别可靠度」的独立判断（0~1），与 verdict 无关——"
    "看得清但确实全对照样可以高 confidence，看不清就低 confidence 并取 unreadable。"
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


async def _generate(
    instruction: str, frame: bytes, cfg: dict, schema: Optional[type] = None
) -> str:
    """调 gemini 多模态客户端 → 原始文本。model per-call 取自 config.roles.vision（禁硬编码）。

    `schema` 仅 check_draft 传（结构化输出硬约束）；look_at_page/observe 不传，保持原行为不扩面。
    """
    client = client_for_role("vision", cfg)
    model = cfg["roles"]["vision"]["model"]
    from google.genai import types  # 延迟导入：MOCK 路径零依赖

    gen_config = None
    if schema is not None:
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=_VISION_TEMPERATURE,
        )
    parts = [instruction, types.Part.from_bytes(data=frame, mime_type="image/jpeg")]
    resp = await client.aio.models.generate_content(
        model=model, contents=parts, config=gen_config
    )
    return resp.text


async def _run(
    instruction: str,
    frame: bytes,
    cfg: Optional[dict],
    schema: Optional[type] = None,
    finalize=None,
):
    """真实路径公共骨架：取 cfg + 按 vision_retry_max 重试 → 返回解析后的结果。

    `schema` 透传给 _generate（仅 check_draft 用结构化输出）。`finalize`（仅 check_draft 传）在
    重试循环内对解析出的 dict 收尾校验（CheckDraftResult 红线 model_validate）——故非法形态抛的
    ValueError 也落在 vision_retry_max 覆盖内重试；耗尽 → RuntimeError，不静默放过非法形态。
    无 finalize 时原样返回 dict（look_at_page/observe 保持原行为）。
    """
    if cfg is None:
        cfg = load_config()
    retry_max = int(cfg.get("session", {}).get("vision_retry_max", 0))
    last_err: Optional[Exception] = None
    for _ in range(retry_max + 1):
        try:
            data = _strip_json(await _generate(instruction, frame, cfg, schema))
            return finalize(data) if finalize is not None else data
        except Exception as e:  # 网络/解析/红线校验失败 → 受 config 重试上限约束重试
            last_err = e
    raise RuntimeError("vision 工具失败（已达 vision_retry_max）") from last_err


async def look_at_page(frame: bytes, cfg: Optional[dict] = None) -> LookAtPageResult:
    """识题/读草稿（契约·视觉 kind=look_at_page）→ LookAtPageResult{text, confidence}。"""
    if is_mock("MOCK_VISION"):
        return LookAtPageResult.model_validate(_first_fixture_payload("vision_look_at_page.jsonl"))
    data = await _run(_LOOK_INSTRUCTION, frame, cfg)
    return LookAtPageResult(text=data["text"], confidence=data["confidence"])


def _finalize_check_draft(data: dict) -> CheckDraftResult:
    """gemini 结构化输出 dict → CheckDraftResult 最终红线校验（kind 工具回填；found_error→error_line）。

    非法形态在此抛 ValueError，由 _run 的 vision_retry_max 重试覆盖。
    """
    return CheckDraftResult.model_validate(
        {
            "kind": "check_draft",
            "verdict": data["verdict"],
            "error_line": data.get("error_line") or None,
            "error_type": data.get("error_type") or None,
            "confidence": data["confidence"],
        }
    )


async def check_draft(frame: bytes, cfg: Optional[dict] = None) -> CheckDraftResult:
    """批改（契约·视觉 kind=check_draft）→ CheckDraftResult{verdict, error_line?, confidence}。

    真分支用 gemini 结构化输出（response_schema=_CheckDraftExtract，绿层硬约束，模型碰不到）：
    schema 内无任何承载答案的字段，结构上无处报答案；末尾 CheckDraftResult 红线再兜形态。
    """
    if is_mock("MOCK_VISION"):
        return CheckDraftResult.model_validate(_first_fixture_payload("vision_check_draft.jsonl"))
    return await _run(
        _CHECK_INSTRUCTION, frame, cfg, schema=_CheckDraftExtract, finalize=_finalize_check_draft
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
