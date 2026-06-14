"""LLM 供应商抽象（跨模块共享基础设施）。支持 gemini / openai 两家（CLAUDE.md 权属规则）。

角色→供应商绑定全在 config.roles（live=gemini 主 / openai 备，vision=gemini）；
密钥按 providers.*.api_key_env 从 .env 取；代码禁硬编码模型名/密钥/base_url（契约·配置）。
"""
