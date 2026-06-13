"""三供应商客户端工厂（deepseek / openai / gemini）。M0 骨架，禁业务逻辑。

统一接口：按角色（planner/vision/asr/tts）从 config 解析 provider+model+key，返回客户端。
- deepseek：OpenAI 兼容协议，复用 openai SDK + providers.deepseek.api_base
- openai：openai SDK
- gemini：google-generativeai
MOCK_LLM=1 / MOCK_PLANNER=1 → 返回不调网络的桩客户端，使模块可独立运行。
"""

SUPPORTED = ("deepseek", "openai", "gemini")


def client_for_role(role: str, cfg: dict):
    """据 config.roles[role] 解析 provider/model/key，返回对应供应商客户端。M1 实现。

    role ∈ {planner, vision, asr, tts}；provider ∈ SUPPORTED。
    """
    raise NotImplementedError("M1：三供应商客户端工厂（按 config 角色绑定）")
