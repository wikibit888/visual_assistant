"""契约十二 · MOCK 开关（PRD §1.5 并行开发，形态 = env）。

每个外部依赖支持 `MOCK_X=1` → 脱依赖、可独立运行，让前后端/各模块并行开发互不阻塞。
约定：未设或非 "1" 即真实路径。新增 MOCK 须登记到 KNOWN_MOCKS + .env.example。

Live 版只剩三个真实外部依赖（自搓 ASR/TTS/planner 已退役 → 对应 MOCK 一并删除）：
  - MOCK_LIVE    —— Live 大脑（Gemini Live / OpenAI realtime）。置 1：中继走脚本/回声桩，
                    不连 realtime API，让后端中继 + 客户端 UI 可脱云联调。
  - MOCK_VISION  —— 视觉工具识别（gemini-2.5-flash）。置 1：工具执行体读 fixture，不抓真帧。
  - MOCK_WEATHER —— weather_get。置 1：返回写死兜底，不调 Open-Meteo。
"""

import os

KNOWN_MOCKS = [
    "MOCK_LIVE",
    "MOCK_VISION",
    "MOCK_WEATHER",
]


def is_mock(name: str) -> bool:
    """该 MOCK 开关是否开启。"""
    return os.getenv(name, "0") == "1"
