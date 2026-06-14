# CLAUDE.md · Visual Assistant v0.1 工程规范（Live 版）

> 写给后续所有 AI 编码会话。**`VisualAssistant-PRD.md` 是已冻结的开发基线**——
> 产品决策一律以 PRD 为准；本文件与 PRD 冲突时，PRD 赢，并须回头修本文件。
> 契约（`contracts/`）是跨进程真理来源，改契约 = 改基线，需决策人授权。

> **架构已从「手搓级联语音链路」整体重构为「Live speech-to-speech」**（本轮，2026-06-14）。
> 旧的 planner 编排循环 / 出站文本护栏 / rails / 工作记忆 store / 自搓 ASR-TTS-VAD 全部退役删除——
> 它们是技术债。新基线见下。

---

## 0. 当前阶段

- **新 M0 已交付**：Live 版契约（一~十四）+ 后端瘦骨架（中继 + 工具执行体 + 技能/供应商）+
  config/.env + fixtures + 本规范 + 前端 shell 骨架。
- 业务逻辑尚未实现（关键 wiring 标 `NotImplementedError("M1 ...")`，MOCK 路径可空跑）。下一步见 `TASKS.md`。
- 验收：`uv run pytest -q` 应全绿（契约层 + fixture 自洽，零外部依赖）。

---

## 1. 技术栈（PRD §3）

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | Chrome 页面 · 原生 ES modules（不打包、不用 Electron） | getUserMedia + 内建 AEC + WebSocket；PCM16k 上送 / 24k 播放 |
| 姿态检测 | MediaPipe Pose（端侧） | 颈/背夹角 + 头部位置双条件；100% 端侧、零云调用 |
| 语音轮次 | **Live 模型原生** | 原生 VAD / barge-in / 断句；双模式：对讲机 PTT（默认）、自由对话 VAD（高光） |
| 后端 | Python ≥3.11 · FastAPI · **单 WebSocket 中继** | 中继音频双向 + 工具执行体（确定性代码）；不复刻编排循环 |
| 大脑 | **Gemini Live**(`gemini-3.1-flash-live-preview`) 主 / OpenAI `gpt-realtime` 备 | ASR + 推理 + TTS 一体；function calling；session memory；proactive audio |
| 视觉工具 | 后端抓帧 + gemini-2.5-flash 识别 | `look_at_page` / `check_draft` / `observe`，返回带 confidence；视觉预算计数 |
| 天气 | Open-Meteo（无 key） | 定位 + 城市/小时缓存 + 失败静默回落 |

物理 2 进程（前端 Chrome + 后端 FastAPI），逻辑分工见 §2。

---

## 2. 模块边界与权属（PRD §4.1）

塌进 Live 模型后，原 A 编排核心 / B 自搓语音的大部分职责没了。新分工：

| 模块 | 职责 | 物理位置 |
|---|---|---|
| **中继 relay** | 客户端 ⇄ 后端 ⇄ Live 会话桥；泵音频双向；翻译控制事件；派发 function_call；注入 posture text | `server/relay/`（`live_bridge.py` + `tool_dispatch.py`） |
| **工具 tools** | `look_at_page/check_draft/observe`（视觉）+ `weather_get`；抓帧识别 + confidence + 视觉预算 + 回落 | `server/tools/` |
| **技能 skills（大脑）** | 按 mode 叠系统提示 profile：分步引导 / 诚实兜底 / 穿搭措辞 / 坐姿 proactive 择时与措辞 | `server/skills/prompts.py` |
| **供应商 llm** | Live 会话客户端 + 视觉客户端工厂（gemini / openai） | `server/llm/providers.py` |
| **前端 UI** | 引导页 + 右上三模式 + 模式内语音切换 + 字幕开关 + 学习坐姿指示器 + 字幕面板 | `web/src/modules/ui.js` + `index.html` |
| **前端语音 voice** | 采播音频（PCM16/24）+ PTT/VAD 切换 + 半双工 mic gate + barge-in | `web/src/modules/voice.js` |
| **前端姿态 posture（D）** | 端侧 MediaPipe 检测 + 放行门控 + gap 闸门 → `posture.alert` | `web/src/modules/posture.js` |
| **前端状态 client_state** | 蓝层确定性：active_problem / reminder_count / mic gate / gap 判定 | `web/src/modules/client_state.js` |

### 铁律（不可违反）
1. **跨进程只走契约**：客户端 ⇄ 后端只用 `contracts/` 的协议（Envelope + 音频二进制帧）；前端只收/发
   `protocol.py` 列的 type，别新铸协议消息。
2. **后端只是中继 + 工具执行体**——编排决策 / 轮次 / ASR / TTS / 打断全在 Live 模型，后端不复刻
   （别再写 planner 循环 / 出站文本护栏 / 状态机）。
3. **确定性三落点，模型碰不到**：① 工具执行体（抓几帧/失败回落/confidence/视觉预算，绿层）；
   ② 客户端状态/闸门（active_problem/reminder_count/gap/mic gate，蓝层）；③ 提示词约束（措辞/择时软上界）。
   **不再有出站文本护栏**——确定性靠工具返回值 + 客户端闸门 + 提示词，不靠拦截模型每个字（PRD §1）。
4. **D 只输出 `posture.alert`**：端侧检测、绝不出声、绝不入工具表（push 事件，非模型能拉的 function call）。
   放行（mode==learning 或 active_problem!=null）+ gap 闸门在客户端；措辞与最终择时交 Live 模型 proactive。
5. **`mode` 是用户 UI 显式选**（学习/生活/开放），不再模型隐式推断。工具子集按 mode 软收窄
   （MODE_TOOLSETS）——是 profile 不是硬护栏；模式误判风险知情接受（PRD §5）。
6. **提示词权属归 skills（E，大脑）**：软逻辑全在系统提示；阈值/模型名禁进 prompt（契约·配置）。
   工具白名单一律取自 `contracts.tools` 单一真源。
7. **无执行类工具**：订外卖/读屏/控设备一律无对应函数 → 模型按提示词「帮不上」（PRD §2/§5）。

---

## 3. MOCK 规则（契约·MOCK）

**每个真实外部依赖支持 `MOCK_X=1`，置 1 即脱依赖、可独立运行。** 见 `.env.example`：
`MOCK_LIVE`（Live 大脑走脚本/回声桩）/ `MOCK_VISION`（视觉工具读 fixture）/ `MOCK_WEATHER`（天气写死兜底）。
判定统一用 `contracts.mock.is_mock("MOCK_X")`。新增 MOCK 须登记到 `contracts/mock.py` + `.env.example`。
目的：前后端 + 各模块并行开发互不阻塞（PRD §1.5）。
> 旧的 `MOCK_ASR/MOCK_TTS/MOCK_PLANNER/MOCK_LLM` 随自搓链路退役，已删。

---

## 4. 配置与密钥权属（契约·配置，**最常被违反，重点盯**）

- **阈值 / 模型名 / 契约值 / 开关 → `config.yaml`。代码中禁硬编码任何阈值或模型名。**
- **密钥 → `.env`（已 gitignore）。`config.yaml` 不放密钥**，只放 `api_key_env` 指向 .env 键名。
- 前端阈值（VAD/姿态/gap/mic-gate）由后端建连经 `config.push` 下发 `posture`/`voice` 两子树，前端不自带魔数。
- 取配置统一走 `contracts.config_schema.load_config()`。

角色绑定（本轮决策，可改 config 切换）：
`live=gemini/gemini-3.1-flash-live-preview`（主；OpenAI gpt-realtime 备）；`vision=gemini/gemini-2.5-flash`。

---

## 5. 本轮决策记录（决策人已确认）

| 项 | 决策 | 依据 |
|---|---|---|
| 架构 | 手搓级联 → **Live speech-to-speech**；编排/语音工程外包给 Live 模型 | PRD 全文（Live 版）；本会话决策人授权「大胆改、当前实现是技术债」 |
| Live 主供应商 | **Gemini Live** 主 + **OpenAI gpt-realtime** 备；deepseek 因无 realtime 退役 | 本会话决策人确认；config 可切 |
| 视觉供应商 | **Gemini 2.5-flash**（deepseek 无多模态） | 沿用；工具执行体侧识别 |
| 前端 | **全量重写** web 为 Live shell 骨架（引导页+右上三模式+模式内语音切换+字幕开关+学习坐姿指示器） | 本会话决策人确认 |
| 本轮范围 | **清空重置为新 M0**：删旧实现 + 重写契约 + 新瘦骨架 + pytest 全绿 + 重写 CLAUDE/TASKS/config | 本会话决策人确认 |
| 坐姿持续阈值 | **30s**（`posture.hunchback_hold_ms=30000`）；PRD §3.2/§11 已对齐 30s | 沿用；回归更长只改 config 一处 |
| 视觉预算 / 重试 | `vision_budget_per_problem=3`（识题1+批改≤2）/ `vision_retry_max=1` | PRD §5 缺省 |

---

## 6. 关键契约速查（详见 `contracts/CONTRACTS.md`）

- 控制信封 `Envelope{type, ts(epoch ms), channel, payload, schema_version}`（**无 turn_id**——Live 管轮次）。
- 音频走 WS **二进制帧**（PCM16 上 / PCM24 下），不裹信封；文本帧恒为信封。
- 三支柱 = `Mode{open,learning,life}`（用户 UI 选）；语音 = `VoiceMode{ptt,free}`（模式内切）。
- 视觉三工具返回带 confidence；`check_draft` 三值 `Verdict{found_error,all_correct,unreadable}`
  （`found_error` 必带 `error_line`）；低置信不再是 verdict，confidence 独立字段，提示词约束处置。
- 工具白名单：`look_at_page/check_draft/observe/weather_get`。**无执行类工具**（越界→「帮不上」）。
- `posture.alert` 不进工具表（push）；视觉按需抓帧（`frame.request/response`）+ 工具执行层预算计数。
- 前端阈值经 `config.push`（posture+voice 子树）下发；前端不自带魔数。

---

## 7. 运行 / 测试

```bash
uv sync                         # 装依赖（pyproject + uv.lock，唯一真相源；首次自动建 .venv）
uv run pytest -q                # 契约层 + fixture 自检（零外部依赖，应全绿）
# M1 起：uv run uvicorn server.main:app --reload    （单 WS 端点 /ws）
# 前端：用任意静态服务器托管 web/（Chrome 打开），需 https 或 localhost 才能拿摄像头/麦克风
```

模块独立开发：置对应 `MOCK_X=1` 即可不接真实供应商跑通本模块。

---

## 8. 给后续会话的纪律

- 动 `contracts/` 或 `config.yaml` 的契约值 = 动基线 → 先确认决策人授权，并同步 `CONTRACTS.md` + fixtures + 测试。
- 写代码前先读 PRD 对应小节与本文件铁律；**演示安全只依赖确定性三落点（工具返回/客户端闸门/提示词），不依赖模型听话**。
- 别再引入已退役的概念：planner 循环 / 出站文本护栏 / rails / 工作记忆 store / 自搓 VAD-ASR-TTS。
- 任务拆解与依赖见 `TASKS.md`；止损线见 PRD §5（语音链路无 mock 退路，真机预实测）。
