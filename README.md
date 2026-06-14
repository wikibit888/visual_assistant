# Visual Assistant v0.1（Live 模式）

一个"看得见、听得懂、自己会编排"的桌面助手：摄像头 + 麦克风实时多模态，由 **Live speech-to-speech
模型**（Gemini Live / OpenAI gpt-realtime）一口气吃掉编排循环与语音工程。三场景——**学习**（作业辅导 +
坐姿守护）、**生活**（天气穿搭 + 日常）、**开放对话**（什么都能聊）。

> 产品基线见 `VisualAssistant-PRD.md`（已冻结）。工程规范见 `CLAUDE.md`。
> 当前进度 = **新 M0（Live 契约 + 后端瘦骨架 + 前端 shell 骨架）**。

## 架构一句话

```
浏览器客户端  ⇄  单 WebSocket  ⇄  后端 FastAPI 中继  ⇄  供应商 Live 会话
（采播音频 + 端侧姿态          （中继音频双向            （ASR+推理+TTS 一体 +
  + 客户端确定性状态/闸门）      + 工具执行体·确定性代码）   function calling + proactive）
```

确定性收敛到**三个落点**（模型碰不到）：① 工具执行体（后端绿层）② 客户端状态/闸门（前端蓝层）
③ 提示词约束（server/skills）。详见 `CLAUDE.md` §2 铁律。

## 快速开始

```bash
uv sync                   # 装依赖（pyproject + uv.lock，唯一真相源；首次自动建 .venv）
uv run pytest -q          # 契约层 + fixture 自检（零外部依赖，应全绿）
cp .env.example .env      # 填供应商密钥；或全程置 MOCK_X=1 脱依赖跑
# M1 起：uv run uvicorn server.main:app --reload   （单 WS /ws）
# 前端：静态服务器托管 web/，Chrome 打开（需 https 或 localhost 拿摄像头/麦克风）
```

## 结构

```
contracts/   跨进程真理来源：WS 协议 + 工具/视觉/天气 schema（见 contracts/CONTRACTS.md）
config.yaml  阈值/模型名/契约值（密钥在 .env，代码禁硬编码）
server/      后端：relay(Live 会话中继 + function_call 派发) / tools(视觉·天气执行体)
             / skills(系统提示 profile·大脑) / llm(供应商工厂)
web/         前端：ui(引导页+三模式+语音切换+字幕+学习坐姿指示器) / voice(采播)
             / posture(端侧 MediaPipe) / client_state(蓝层确定性状态)
tests/       契约校验 + fixtures（WS 协议每 type ≥1 样例；verdict 三值各一条）
TASKS.md     M1–M6 里程碑（三条并行轨：契约/后端/前端）
```

## 模型供应商

`live=gemini/gemini-3.1-flash-live-preview`（主；OpenAI gpt-realtime 备），`vision=gemini/gemini-2.5-flash`。
角色→供应商绑定在 `config.roles`，三处可切换。MOCK：`MOCK_LIVE / MOCK_VISION / MOCK_WEATHER`。
