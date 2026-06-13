"""契约七 · 配置与密钥（PRD §7.7，形态 = 文件）。

铁律（权属规则）：**密钥只进 .env；阈值/模型名/契约值进 config.yaml；代码中禁硬编码。**
本文件提供：① 期望键清单（供评审/启动自检）；② 极薄 loader（基础设施，非业务逻辑）。

config.yaml 顶层段：providers / roles / orchestration / rails / turn_state / posture /
                    weather / answer_guard。各段含义见 config.yaml 行内注释与对应契约。
"""

import os

# 按所选 provider 子集校验（M0 默认：planner=deepseek，vision/asr/tts=gemini）。
ENV_KEYS = ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"]

# config.yaml 必备顶层段（启动自检用）。
REQUIRED_CONFIG_SECTIONS = [
    "providers",
    "roles",
    "orchestration",
    "rails",
    "turn_state",
    "posture",
    "weather",
    "answer_guard",
]


def load_config(path: str = "config.yaml") -> dict:
    """读取 config.yaml + .env（基础设施）。缺依赖时给出清晰报错，不静默吞。"""
    try:
        import yaml  # 延迟导入，未装依赖时报错更清晰
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("缺少 pyyaml，请 pip install -r requirements.txt") from e

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # python-dotenv 可选；缺失则依赖进程环境变量
        pass

    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到配置文件 {path}（契约七）")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg
