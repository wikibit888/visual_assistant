"""LLM 供应商抽象（跨模块共享）。支持 deepseek / openai / gemini 三家（CLAUDE.md 权属规则）。

角色→供应商绑定全在 config.roles（planner=deepseek，vision/asr/tts=gemini 占位）；
密钥按 providers.*.api_key_env 从 .env 取；代码禁硬编码模型名/密钥/base_url。
"""
