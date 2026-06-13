# Visual Assistant v0.1

一个"看得见、听得懂、自己会编排、还管得住自己"的桌面助手：摄像头 + 麦克风实时多模态，
三场景——**学习**（作业辅导 + 坐姿守护）、**生活**（天气穿搭 + 日常）、**开放对话**（什么都能聊）。

> 产品基线见 `VisualAssistant-PRD.md`（已冻结）。工程规范见 `CLAUDE.md`。当前进度 = **M0（契约+骨架）**。

## 快速开始

```bash
uv sync                   # 装依赖（pyproject + uv.lock，唯一真相源；首次自动建 .venv）
uv run pytest -q          # 契约层 + fixture 自检（零外部依赖，应全绿）
cp .env.example .env      # 填供应商密钥；或全程置 MOCK_X=1 脱依赖跑
```

## 结构

```
contracts/   跨模块真理来源：契约一~十 + 工具注册表（见 contracts/CONTRACTS.md）
config.yaml  阈值/模型名/契约值（密钥在 .env，代码禁硬编码）
server/      后端：a_core(编排+护栏) / b_voice / c_vision / e_skills / llm(三供应商)
web/         前端：b_voice(语音) + d_posture(端侧姿态)
tests/       契约校验 + fixtures（每契约≥1 样例，四值 verdict 各一条）
TASKS.md     M1–M4 任务卡
```

## 模型供应商

deepseek / openai / gemini 三家，角色→供应商绑定在 `config.roles`：
planner=deepseek-chat（PRD 锁），vision=gemini-2.5-flash，asr/tts=gemini 生态占位（M1 定）。
