"""供应商客户端工厂（Live 版：gemini / openai）。按 config 角色绑定解析。

统一接口 `client_for_role(role, cfg)`，role ∈ {live, vision}：
- 从 `config.roles[role]` 取 provider/model；从 `config.providers[provider]` 取 api_base / api_key_env；
  密钥从 .env 环境变量读（禁硬编码模型名/密钥/base_url——契约·配置）。
- live   →  Gemini Live（`google.genai` 的 `client.aio.live`）或 OpenAI realtime（gpt-realtime）。
- vision →  Gemini 多模态（`gemini-2.5-flash`，单帧识别 → {…, confidence}）。
- MOCK_LIVE=1（live 角色）/ MOCK_VISION=1（vision 角色）→ 返回不联网桩，模块可独立跑（契约·MOCK）。

真实 SDK 一律延迟导入：MOCK 路径零外部依赖、可独立运行。本工厂只**造客户端**，不持有会话——
Live 会话的建立/泵音频/派发 function_call 在 server/relay/live_bridge.py。
"""

from __future__ import annotations

import os

from contracts.mock import is_mock

SUPPORTED = ("gemini", "openai")

# 角色 → 触发 MOCK 的开关（契约·MOCK）。
_MOCK_ENV_FOR_ROLE = {"live": "MOCK_LIVE", "vision": "MOCK_VISION"}


class MockClient:
    """不联网桩客户端（MOCK_LIVE / MOCK_VISION=1）。记录角色与 provider/model 绑定意图，不发网络。

    具体桩行为（脚本化 Live 回声 / 读 fixture 的 vision）由 relay / tools 各自实现；本桩只占位。
    """

    is_mock = True

    def __init__(self, role: str, provider: str, model: str) -> None:
        self.role = role
        self.provider = provider
        self.model = model

    def __repr__(self) -> str:
        return f"MockClient(role={self.role!r}, provider={self.provider!r}, model={self.model!r})"


def _resolve(role: str, cfg: dict):
    """从 config 解析 (provider, model, provider_cfg)；非法即清晰报错，不静默吞。"""
    roles = cfg.get("roles", {}) or {}
    if role not in roles:
        raise KeyError(f"config.roles 缺角色 {role!r}（契约·配置）")
    role_cfg = roles[role] or {}
    provider = role_cfg.get("provider")
    model = role_cfg.get("model")
    if provider not in SUPPORTED:
        raise ValueError(f"角色 {role!r} 的 provider={provider!r} 不在 {SUPPORTED}（契约·配置）")
    if not model:
        raise ValueError(f"角色 {role!r} 未配置 model（契约·配置：模型名进 config.yaml）")
    providers = cfg.get("providers", {}) or {}
    if provider not in providers:
        raise KeyError(f"config.providers 缺 {provider!r}（契约·配置）")
    return provider, model, providers[provider] or {}


def _should_mock(role: str) -> bool:
    """该角色是否走不联网桩（MOCK_LIVE→live；MOCK_VISION→vision）。"""
    env = _MOCK_ENV_FOR_ROLE.get(role)
    return bool(env) and is_mock(env)


def client_for_role(role: str, cfg: dict):
    """据 config.roles[role] 解析 provider/model/key，返回对应供应商客户端。

    role ∈ {live, vision}；provider ∈ SUPPORTED。MOCK 命中 → MockClient；
    否则按 provider 延迟导入 SDK 构造真实客户端（gemini 用 google.genai；openai 用 openai SDK）。
    """
    provider, model, provider_cfg = _resolve(role, cfg)

    if _should_mock(role):
        return MockClient(role, provider, model)

    api_key_env = provider_cfg.get("api_key_env")
    api_key = os.getenv(api_key_env) if api_key_env else None
    api_base = provider_cfg.get("api_base")

    if provider == "gemini":
        # 新版 SDK：Developer API 传 api_key 即用 generativelanguage 后端；model 为 per-call 入参
        # （调用方从 config 传）。Live 经 client.aio.live、vision 经 client.aio.models——皆用本 Client。
        from google import genai

        return genai.Client(api_key=api_key)
    if provider == "openai":
        # OpenAI realtime（gpt-realtime）：同一 SDK + config 提供的 api_base。
        from openai import OpenAI

        return OpenAI(api_key=api_key, base_url=api_base)

    raise ValueError(f"不支持的 provider={provider!r}")  # _resolve 已校验，理论不达
