"""C · vision.* 工具实现（契约三）。read_problem 已实现（M1-08）。

read_problem → ReadProblemResult{problem_text, confidence}
  · 真实路径：client_for_role("vision", cfg) → gemini 多模态；模型名取自
    config.roles.vision（禁硬编码，契约七）。重试上限 = config.orchestration.vision_retry_max。
  · MOCK_VISION=1：读 tests/fixtures/vision_read_problem.jsonl（脱依赖、可独立运行，契约六）。
check_draft → CheckDraftResult{verdict(四值), error_line?, error_type?, confidence}（M2）
observe     → ObserveResult{description, confidence}（M2-04，结构镜像 read_problem）
  · 真实路径：client_for_role("vision", cfg) → gemini 多模态；hint 非空时拼进抽取指令。
  · MOCK_VISION=1：读 tests/fixtures/vision_observe.jsonl 首条 payload（脱依赖，契约六）。
confidence 原样返回，是否播报交 A 的置信门控裁决——C 不自决（PRD §7.4 / CLAUDE.md 铁律）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from contracts.vision import ObserveResult, ReadProblemResult
from contracts.config_schema import load_config
from contracts.mock import is_mock
from server.llm.providers import client_for_role

# 仓库根（server/c_vision/vision_tools.py → parents[2]）→ 定位 MOCK fixture。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_READ_PROBLEM_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "vision_read_problem.jsonl"
_OBSERVE_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "vision_observe.jsonl"

# C 内部「结构化抽取指令」——非技能/路由/人设 prompt（那类权属归 E）；
# 仅约束 gemini 产出 {problem_text, confidence} 形态，便于解析回契约三。
_READ_PROBLEM_INSTRUCTION = (
    "你是题面识别器。原样转写画面中用户指向的题目文本，不解题、不补全、不翻译。"
    "同时给出识别完整性的置信度 confidence（0~1）。"
    '只输出 JSON：{"problem_text": "...", "confidence": 0.0}'
)

# C 内部「结构化抽取指令」（同 read_problem，非 E 的人设/技能/措辞 prompt）；
# 仅约束 gemini 用一句话客观描述画面中的穿搭/物体 + confidence，便于解析回契约三。
_OBSERVE_INSTRUCTION = (
    "你是画面观察器。用一句话客观描述画面中的穿搭或随手物体，只陈述所见、不评价、不建议。"
    "同时给出描述可靠性的置信度 confidence（0~1）。"
    '只输出 JSON：{"description": "...", "confidence": 0.0}'
)


def _mock_read_problem() -> ReadProblemResult:
    """MOCK_VISION：读 fixture 首条 payload → 合规 ReadProblemResult（零外部依赖）。"""
    with _READ_PROBLEM_FIXTURE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                payload = json.loads(line)["payload"]
                return ReadProblemResult.model_validate(payload)
    raise RuntimeError(f"MOCK_VISION fixture 为空：{_READ_PROBLEM_FIXTURE}")


def _parse_read_problem(text: str) -> ReadProblemResult:
    """模型文本 → 契约三 ReadProblemResult；confidence 原样保留，交 A 护栏裁决。"""
    raw = text.strip()
    if raw.startswith("```"):                 # 容忍 ```json fenced``` 包裹
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
    data = json.loads(raw)
    return ReadProblemResult.model_validate(
        {"problem_text": data["problem_text"], "confidence": data["confidence"]}
    )


async def _generate(client, frame) -> str:
    """调 gemini 多模态客户端 → 原始文本。client 已由 config.roles.vision 绑定模型名。"""
    parts: list = [_READ_PROBLEM_INSTRUCTION]
    if frame is not None:                     # frame 形态（PIL/blob）由上游抓帧侧给定
        parts.append(frame)
    resp = await client.generate_content_async(parts)
    return resp.text


def _mock_observe() -> ObserveResult:
    """MOCK_VISION：读 fixture 首条 payload → 合规 ObserveResult（零外部依赖）。"""
    with _OBSERVE_FIXTURE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                payload = json.loads(line)["payload"]
                return ObserveResult.model_validate(payload)
    raise RuntimeError(f"MOCK_VISION fixture 为空：{_OBSERVE_FIXTURE}")


def _parse_observe(text: str) -> ObserveResult:
    """模型文本 → 契约三 ObserveResult；confidence 原样保留，交 A 护栏裁决。"""
    raw = text.strip()
    if raw.startswith("```"):                 # 容忍 ```json fenced``` 包裹
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
    data = json.loads(raw)
    return ObserveResult.model_validate(
        {"description": data["description"], "confidence": data["confidence"]}
    )


async def _generate_observe(client, frame, hint: Optional[str]) -> str:
    """调 gemini 多模态客户端 → 原始文本。hint 非空时拼进抽取指令（提示看穿搭/某物体）。"""
    instruction = _OBSERVE_INSTRUCTION
    if hint:                                  # hint 仅缩小观察焦点，不引入 E 的措辞/人设
        instruction = f"{instruction}\n关注：{hint}"
    parts: list = [instruction]
    if frame is not None:                     # frame 形态（PIL/blob）由上游抓帧侧给定
        parts.append(frame)
    resp = await client.generate_content_async(parts)
    return resp.text


async def read_problem(frame=None, cfg: Optional[dict] = None) -> ReadProblemResult:
    """识题（契约三 kind=read_problem）→ ReadProblemResult{problem_text, confidence}。

    MOCK_VISION=1 → 读 fixture；否则经 client_for_role("vision", cfg) 走 gemini 多模态
    （模型名取自 config.roles.vision，禁硬编码）。失败按 config.orchestration.vision_retry_max
    重试。confidence 原样返回，C 不自决是否播报（交 A 置信门控，PRD §7.4）。
    """
    if is_mock("MOCK_VISION"):
        return _mock_read_problem()

    if cfg is None:
        cfg = load_config()
    client = client_for_role("vision", cfg)

    retry_max = int(cfg.get("orchestration", {}).get("vision_retry_max", 0))
    last_err: Optional[Exception] = None
    for _ in range(retry_max + 1):            # retry_max 次重试 = retry_max + 1 次尝试
        try:
            return _parse_read_problem(await _generate(client, frame))
        except Exception as e:                # 网络/解析失败 → 受 config 重试上限约束重试
            last_err = e
    raise RuntimeError("read_problem 失败（已达 vision_retry_max）") from last_err


async def check_draft():
    raise NotImplementedError("M2：批改（四值 verdict）")


async def observe(hint: Optional[str] = None, cfg: Optional[dict] = None) -> ObserveResult:
    """穿搭/即兴物体识别（契约三 kind=observe）→ ObserveResult{description, confidence}。

    MOCK_VISION=1 → 读 fixture；否则经 client_for_role("vision", cfg) 走 gemini 多模态
    （模型名取自 config.roles.vision，禁硬编码）。hint 非空时拼进抽取指令缩小观察焦点。
    失败按 config.orchestration.vision_retry_max 重试。confidence 原样返回，C 不自决是否
    播报（交 A 置信门控，PRD §7.4）。结构镜像 read_problem。
    """
    if is_mock("MOCK_VISION"):
        return _mock_observe()

    if cfg is None:
        cfg = load_config()
    client = client_for_role("vision", cfg)

    retry_max = int(cfg.get("orchestration", {}).get("vision_retry_max", 0))
    last_err: Optional[Exception] = None
    for _ in range(retry_max + 1):            # retry_max 次重试 = retry_max + 1 次尝试
        try:
            return _parse_observe(await _generate_observe(client, None, hint))
        except Exception as e:                # 网络/解析失败 → 受 config 重试上限约束重试
            last_err = e
    raise RuntimeError("observe 失败（已达 vision_retry_max）") from last_err
