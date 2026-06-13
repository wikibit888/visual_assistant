"""三供应商客户端工厂（deepseek / openai / gemini）。M1-01：按 config 角色绑定解析。

统一接口 `client_for_role(role, cfg)`：
- 从 `config.roles[role]` 取 provider/model；从 `config.providers[provider]` 取
  api_base / api_key_env；密钥从 .env 环境变量读（禁硬编码模型名/密钥/base_url——契约七）。
- deepseek：OpenAI 兼容协议，复用 openai SDK + providers.deepseek.api_base
- openai：openai SDK
- gemini：google-generativeai
- MOCK_LLM=1（通用）/ MOCK_PLANNER=1（planner 角色）→ 返回不联网桩，模块可独立跑（契约六）。

真实 SDK 一律延迟导入：MOCK 路径零外部依赖、可独立运行。
"""

from __future__ import annotations

import os

from contracts.mock import is_mock

SUPPORTED = ("deepseek", "openai", "gemini")


class MockLLMClient:
    """不联网桩客户端（MOCK_LLM / MOCK_PLANNER=1）。

    M1-01 仅作占位：记录角色与 provider/model 绑定意图，不发起任何网络调用。
    具体补全（脚本化输出等）由 A planner / E 在 M1-02+ 各自实现。
    """

    is_mock = True

    def __init__(self, role: str, provider: str, model: str) -> None:
        self.role = role
        self.provider = provider
        self.model = model

    def __repr__(self) -> str:
        return (
            f"MockLLMClient(role={self.role!r}, provider={self.provider!r}, "
            f"model={self.model!r})"
        )


def _resolve(role: str, cfg: dict):
    """从 config 解析 (provider, model, provider_cfg)；非法即清晰报错，不静默吞。"""
    roles = cfg.get("roles", {}) or {}
    if role not in roles:
        raise KeyError(f"config.roles 缺角色 {role!r}（契约七）")
    role_cfg = roles[role] or {}
    provider = role_cfg.get("provider")
    model = role_cfg.get("model")
    if provider not in SUPPORTED:
        raise ValueError(
            f"角色 {role!r} 的 provider={provider!r} 不在 {SUPPORTED}（契约七）"
        )
    if not model:
        raise ValueError(f"角色 {role!r} 未配置 model（契约七：模型名进 config.yaml）")
    providers = cfg.get("providers", {}) or {}
    if provider not in providers:
        raise KeyError(f"config.providers 缺 {provider!r}（契约七）")
    return provider, model, providers[provider] or {}


def _should_mock(role: str) -> bool:
    """该角色是否走不联网桩：MOCK_LLM 通用；MOCK_PLANNER 仅对 planner 角色。"""
    if is_mock("MOCK_LLM"):
        return True
    if role == "planner" and is_mock("MOCK_PLANNER"):
        return True
    return False


def client_for_role(role: str, cfg: dict):
    """据 config.roles[role] 解析 provider/model/key，返回对应供应商客户端。

    role ∈ {planner, vision, asr, tts}；provider ∈ SUPPORTED。
    MOCK 命中 → MockLLMClient；否则按 provider 延迟导入 SDK 构造真实客户端。
    """
    provider, model, provider_cfg = _resolve(role, cfg)

    if _should_mock(role):
        return MockLLMClient(role, provider, model)

    api_key_env = provider_cfg.get("api_key_env")
    api_key = os.getenv(api_key_env) if api_key_env else None
    api_base = provider_cfg.get("api_base")

    if provider in ("deepseek", "openai"):
        # deepseek 走 OpenAI 兼容协议：同一 SDK + config 提供的 api_base。
        from openai import OpenAI

        return OpenAI(api_key=api_key, base_url=api_base)
    if provider == "gemini":
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        return genai.GenerativeModel(model)

    raise ValueError(f"不支持的 provider={provider!r}")  # _resolve 已校验，理论不达
