# CLAUDE.md · Visual Assistant v0.1 工程规范

> 写给后续所有 AI 编码会话。**`VisualAssistant-PRD.md` 是已冻结的开发基线**——
> 产品决策一律以 PRD 为准；本文件与 PRD 冲突时，PRD 赢，并须回头修本文件。
> 契约（`contracts/`）是跨模块真理来源，改契约 = 改基线，需决策人授权。

---

## 0. 当前阶段

- **M0 已交付**：契约一~十 + 工具注册表 + 仓库骨架 + config/.env + fixtures + 本规范。
- 业务逻辑尚未实现（骨架函数 `raise NotImplementedError("Mx ...")`）。下一步见 `TASKS.md`。
- 验收：`pytest -q` 应全绿（契约层 + fixture 自洽，零外部依赖）。

---

## 1. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | Chrome 页面 · 原生 ES modules（不打包、不用 Electron） | getUserMedia + 内建 AEC + MediaPipe JS（姿态）+ vad-web（VAD） |
| 后端 | Python ≥3.9（建议 3.11）· FastAPI · **单 WebSocket** | 信封收发；`uvicorn` 起服务 |
| 数据契约 | Pydantic v2 | `contracts/` 全量模型 |
| 配置 | `config.yaml`（阈值/模型名/契约值）+ `.env`（密钥） | 见 §4 权属规则 |
| 模型供应商 | **deepseek / openai / gemini 三家**（`server/llm/providers.py` 抽象） | 角色→供应商绑定在 `config.roles` |

物理 2 进程（前端 Chrome + 后端 FastAPI），逻辑 5 模块（A–E）。

---

## 2. 模块边界与权属（PRD §7.1）

| 模块 | 角色 / 权属 | 物理位置 |
|---|---|---|
| **A 编排核心** | agent 编排循环、工作记忆、dispatch、**确定性护栏**、间隙仲裁、turn_id、rails 切换、工具注册表 | `server/a_core/` |
| **B 语音 I/O** | 前端 VAD/PTT/播放队列/半双工 gate + 后端云 ASR/TTS 适配 | `web/src/modules/b_voice.js` + `server/b_voice/` |
| **C 视觉服务** | `vision.*` 工具实现、重试 | `server/c_vision/` |
| **D 姿态守护** | **纯端侧**，只发 `posture.alert`，不出声、不入 agent loop | `web/src/modules/d_posture.js` |
| **E 技能/提示词库** | 引导/穿搭/小结/开放对话 prompt、坐姿话术模板、答案护栏词表；**提示词权属归 E** | `server/e_skills/` |
| (llm) | 三供应商客户端工厂（跨模块共享基础设施） | `server/llm/` |

### 铁律（不可违反）
1. **跨模块只走总线消息（契约一 `Envelope`）；禁互相 import 对方内部对象。**
2. `gap.open` 只由 **A** 广播；`tts.say/stop` 只发 **B**；**D 只输出 `posture.alert`**。
3. **扬声器每个字都经 A 的确定性护栏**；planner 不得绕过护栏直产 TTS；护栏不得被提示词关闭。
4. **E 不得内嵌路由**——路由 = planner 工具选择，无独立意图分类器。
5. **护栏在编排循环之外、不可被 planner 覆盖**（设计脊梁：planner 提议，确定性护栏裁决）。
6. **非确定性只许出现在「语言与策略」，不许出现在「立场与时序」**（PRD §1.3）。

---

## 3. MOCK 规则（契约六）

**每个模块 + 每个工具都必须支持 `MOCK_X=1`，置 1 即脱依赖、可独立运行。** 见 `.env.example`：
`MOCK_VISION / MOCK_WEATHER / MOCK_ASR / MOCK_TTS / MOCK_PLANNER / MOCK_LLM`。
判定统一用 `contracts.mock.is_mock("MOCK_X")`。新增 MOCK 须登记到 `contracts/mock.py` + `.env.example`。
目的：AI agent 可并行写独立模块，互不阻塞（PRD §1.5 / §10）。

---

## 4. 配置与密钥权属（契约七，**最常被违反，重点盯**）

- **阈值 / 模型名 / 契约值 / 开关 → `config.yaml`。代码中禁硬编码任何阈值或模型名。**
- **密钥 → `.env`（已 gitignore）。`config.yaml` 不放密钥**，只放 `api_key_env` 指向 .env 键名。
- 前端阈值（VAD/姿态等）由后端从 config 下发，前端不自带魔数。
- 取配置统一走 `contracts.config_schema.load_config()`。

三供应商角色绑定（M0 决策，可改 config 切换）：
`planner=deepseek/deepseek-chat`（PRD 锁，温度0+结构化）；`vision=gemini/gemini-2.5-flash`；
`asr/tts=gemini 生态占位`（M1 定具体 STT/TTS）。

---

## 5. M0 决策记录（决策人已确认）

| 项 | 决策 | 依据 |
|---|---|---|
| 视觉供应商 | **Gemini 2.5-flash**（deepseek 无多模态） | 本会话决策人确认；config 三家可切 |
| ASR/TTS 供应商 | **Gemini 生态占位**，M1 定具体型号 | 同上；契约二只定消息，供应商 M1 可调 |
| 置信门控阈值 | `confidence_gate=0.6` | PRD 未给值，缺省 |
| 视觉重试上限 | `vision_retry_max=1` | PRD 未给值，缺省 |
| planner 软超时 | `800ms` | PRD §7.2 |

### ⚠ 传播待办（偏离 PRD，决策人已授权）
- **坐姿持续阈值 = 30s**（`config.posture.hunchback_hold_ms=30000`），**偏离 PRD §3.2/S2-01 冻结的 90–120s**。
  决策人本会话授权采用 30s（便于演示导演触发）。**后续若回归 PRD 口径需改 config 一处即可。**
  注意：30s 比 PRD 更短，低头写字误判风险上升——M5 须确认演示中不误触（PRD §11 坐姿口径）。

---

## 6. 关键契约速查（详见 `contracts/CONTRACTS.md`）

- 信封 `Envelope{type, ts(epoch ms), turn_id("t-000123"), channel, payload}` + `schema_version`。
- 四值 verdict：`found_error / all_correct / unreadable / low_confidence`（`found_error` 必带 `error_line`）。
- planner 结构化输出 `PlannerOutput{kind∈{answer,tool_calls,clarify}, mode∈{open,learning,life}, tools, text}`；**自由度只在 text**。
- 工具白名单：`read_problem/check_draft/observe/weather_get/memory_note/memory_recall`。**无执行类工具**（越界→「帮不上」）。
- 工作记忆仅内存不落盘；`posture.*` 不进工具表（push，不可被 agent 拉成自由动作）。
- rails = `config.rails`（与 agentic 同代码、config 切换）；`forced_tool_sequence` = 工具序 + answer 节点混合。

---

## 7. 运行 / 测试

```bash
python3 -m pip install -r requirements.txt
pytest -q                       # 契约层 + fixture 自检（零外部依赖，应全绿）
# M1 起：uvicorn server.main:app --reload    （单 WS 端点 /ws）
# 前端：用任意静态服务器托管 web/（Chrome 打开），需 https 或 localhost 才能拿摄像头/麦克风
```

模块独立开发：置对应 `MOCK_X=1` 即可不接真实供应商跑通本模块。

---

## 8. 给后续会话的纪律

- 动 `contracts/` 或 `config.yaml` 的契约值 = 动基线 → 先确认决策人授权，并同步 `CONTRACTS.md` + fixtures + 测试。
- 写代码前先读 PRD 对应小节与本文件铁律；演示安全只依赖「确定性地板」，不依赖 planner。
- 任务拆解与依赖见 `TASKS.md`；里程碑止损线见 PRD §10。
