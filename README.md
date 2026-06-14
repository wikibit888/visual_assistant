# Visual Assistant v0.1（Live 版）

一个**「看得见、听得懂、会自己编排」的桌面语音助手**：摄像头 + 麦克风实时多模态，由 **Live
speech-to-speech 模型**（Gemini Live）一体完成 ASR + 推理 + TTS，直接和你语音对话、必要时「看一眼」画面。

> 产品基线 `VisualAssistant-PRD.md`（已冻结）；工程规范 `CLAUDE.md`；跨进程契约 `contracts/CONTRACTS.md`；里程碑 `TASKS.md`。
> **进度：M0–M4 已交付并合入 `main`**（开放对话 / 学习 / 生活三动线 + 真机闭环）；M5 加固、M6 彩排待做。

---

## 一、产品定位

桌前的一个语音助手，围绕**三支柱**（用户在右上角显式切换，不靠模型猜）：

| 模式 | 定位 | 一句话 |
|---|---|---|
| **开放对话** open（基座） | 什么都能聊，能看画面 | 随口聊、即兴问，接得住任意话题 |
| **学习** learning | 作业辅导 + 坐姿守护 | 分步引导、**绝不直接报答案**；驼背了轻轻提醒 |
| **生活** life | 天气穿搭 + 日常帮手 | 看你穿什么 + 拿天气 → 给「加件外套/带伞」这种**能照做的动作** |

**设计铁律**：所有「确定性」收敛到三个**模型碰不到**的落点——① 工具执行体（后端绿层）② 客户端状态/闸门（前端蓝层）③ 提示词约束（`server/skills`）。演示安全只依赖这三处，不依赖模型「听话」。

---

## 二、实现功能（已交付）

- **实时语音对话**：对讲机 PTT（默认，按住说话松手发）/ 自由对话 VAD（免按、AI 说话时可打断 barge-in）；可运行时切换；字幕开关；语音不便时**文字输入兜底**。
- **看画面（视觉工具，后端抓帧 + `gemini-2.5-flash` 识别，带 confidence + 单题视觉预算）**：
  - `look_at_page` 识题 / 读草稿原文
  - `check_draft` 批改：三值 `found_error / all_correct / unreadable`，**只定位错误行、绝不写出正确答案**（gemini 结构化输出硬约束，结构上无处安放答案）
  - `observe` 看穿搭 / 手里的物体
- **学习模式**：
  - 分步引导阶梯（方向 → 操作 → 示范，逐级、**绝不顺嘴报答案**）+ 诚实批改口径
  - **坐姿守护**：端侧 MediaPipe Pose 双条件（颈/背夹角 + 头前伸）持续 30s 检测（**100% 端侧、零云调用**）→ 客户端放行门控（仅学习/有活跃题）+ 缝隙择时 + 冷却 + 「第 N 次」计数 → 模型在自然缝隙轻提醒（措辞在模型、计数与择时确定性在客户端）
- **生活模式**：`observe` + `weather_get`（Open-Meteo 真实接口，无 key，坐标/小时缓存，任何失败静默回落默认城市）融合 → **具体到行动**的穿搭/出行建议，默认不念温度数字/地名。
- **运行时切换**：右上切模式（换系统提示 profile + 工具子集，重开会话）/ 模式内切语音 / 字幕开关。
- **韧性与降级**：Live 连接瞬时错误（弱网握手 reset）自动重试；单个工具失败被隔离、不掀整条会话；Live 断流 → 字幕 + 文字兜底；错误提示在链路恢复时**自愈**消失。
- **MOCK 离线开发**：`MOCK_LIVE / MOCK_VISION / MOCK_WEATHER=1` 即脱真实依赖，前后端可独立联调。

> 待做：M5（断流/超预算/越界等降级路径专测）、M6（彩排）。

---

## 三、技术栈 / 架构

```
浏览器客户端  ⇄  单 WebSocket  ⇄  后端 FastAPI 中继  ⇄  Gemini Live 会话
（采播音频 PCM16↑/24↓        （泵音频双向 + 工具执行体    （ASR+推理+TTS 一体 +
  + 端侧 MediaPipe 姿态        ·确定性代码 + 提示词）        function calling + proactive）
  + 客户端确定性状态/闸门）
```

- **前端**：Chrome 原生 ES modules（不打包、不用框架）+ `getUserMedia` + WebSocket；MediaPipe Pose 端侧。
- **后端**：Python ≥ 3.11 · FastAPI 单 WS 中继 + 工具执行体；`uv` 管理依赖。
- **大脑/工具**：Gemini Live（`gemini-3.1-flash-live-preview`）；视觉 `gemini-2.5-flash`；天气 Open-Meteo。
- 角色→供应商绑定在 `config.yaml` 的 `roles`，可切换；阈值/模型名进 `config.yaml`，密钥进 `.env`，代码禁硬编码。

---

## 四、用户使用方法

### 1) 安装
```bash
uv sync                  # 装依赖（pyproject + uv.lock，首次自动建 .venv）
cp .env.example .env     # 填 GEMINI_API_KEY（Open-Meteo 无需 key）
uv run pytest -q         # 自检：应全绿（47 passed）
```

### 2) 启动
```bash
uv run uvicorn server.main:app
```
后端同时把前端静态托管在 `/`。用 **Chrome** 打开 **http://localhost:8000/** 即可
（`localhost` 是安全上下文，浏览器才允许用摄像头/麦克风）。

### 3) 上手流程
1. **引导页**：选模式（学习 / 生活 / 开放）→ 点「进入」→ 授权摄像头 + 麦克风。
2. **说话**：默认**对讲机**——按住底部按钮说、松手即发；或右上切**自由对话**免按直接说（AI 说话时可打断）。
3. **随时**：右上切模式 / 开关字幕；语音不便时用底部**文字输入框**兜底。
4. **学习**：把题或草稿对准摄像头，说「帮我看看这道题 / 检查一下」；驼背持续约 30s 会被**轻轻提醒**（仅学习模式）。
5. **生活**：问「今天穿这样行吗 / 要带伞吗」，AI 看你穿搭 + 天气给可执行建议。

### 4) 离线 / 无密钥开发
`.env` 里把 `MOCK_LIVE=1`（必要时加 `MOCK_VISION=1` / `MOCK_WEATHER=1`）→ 脱真实供应商，前后端动线可空跑联调。

### 5) 网络说明（China → Google）
Gemini（语音/视觉）需能访问 Google。若你用代理（Clash 等）：
- 经 **SOCKS 代理**时已内置 `python-socks` + `socksio`（websockets/httpx 双后端）。
- **若代理挂了而机器能直连 Google**：启动前 `unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy` 走直连即可。
- 天气（open-meteo）同理；连不上会**自动回落默认城市**、不阻塞主链路。

### 6) 用手机当摄像头（可选，方便拍摄）
无需改代码：**Mac + iPhone 用「连续互通相机」**，或装 DroidCam / Camo 把手机变成系统摄像头 → 进入授权时在浏览器摄像头列表里选它即可，画面会同步到 PC 并被姿态检测/抓帧透明复用。

---

## 五、目录结构

```
contracts/   跨进程真理来源：WS 协议 + 工具/视觉/天气/会话 schema（见 CONTRACTS.md）
config.yaml  阈值 / 模型名 / 契约值（密钥在 .env，代码禁硬编码）
server/      后端：relay(Live 会话中继 + function_call 派发) / tools(视觉·天气执行体)
             / skills(系统提示 profile·大脑) / llm(供应商工厂)
web/         前端：ui(引导页+三模式+语音切换+字幕+坐姿指示器) / voice(采播+PTT/VAD)
             / posture(端侧 MediaPipe) / client_state(蓝层确定性) / worklets(PCM 采集/播放)
tests/       契约校验 + fixtures + 工具真路径单测
```
