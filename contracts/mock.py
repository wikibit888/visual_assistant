"""契约六 · MOCK 开关（PRD §7.7，形态 = env）。

每模块 + 每工具支持 `MOCK_X=1` → 该模块/工具脱依赖、可独立运行（PRD 范围声明）。
约定：未设或非 "1" 即真实路径。新增 MOCK 须登记到 KNOWN_MOCKS + .env.example。
"""

import os

KNOWN_MOCKS = [
    "MOCK_VISION",    # C 视觉工具读 fixture
    "MOCK_WEATHER",   # weather.get 返回写死兜底
    "MOCK_ASR",       # B ASR 适配器
    "MOCK_TTS",       # B TTS 适配器
    "MOCK_PLANNER",   # A planner 走固定脚本输出（不调 LLM）
    "MOCK_LLM",       # E/通用 LLM 调用
]


def is_mock(name: str) -> bool:
    """该 MOCK 开关是否开启。"""
    return os.getenv(name, "0") == "1"
