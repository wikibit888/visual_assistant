# 契约索引（Live 版 · 对照 PRD §3 / §4）

> 这是跨进程的**真理来源**。改动任何契约 = 改动基线，需决策人授权 + 同步 fixtures + CLAUDE.md。
> 架构（PRD §4.1）：客户端 ⇄ 单 WebSocket ⇄ 后端中继 ⇄ 供应商 Live 会话。编排循环 + VAD/打断/
> ASR/TTS 全塌进 Live 模型；确定性收敛到**三个落点**——① 工具执行体（绿层）② 客户端状态/闸门
> （蓝层）③ 提示词约束（server/skills）。

## 契约清单

| # | 契约 | 落点文件 | 关键符号 |
|---|---|---|---|
| 一 | 控制信封 + 协议枚举 | `envelope.py` + `protocol.py` | `Envelope` / `MessageType` / `Channel` |
| 二 | 会话生命周期 + 模式 | `session.py` | `Mode` / `VoiceMode` / `SessionStart` / `SessionUpdate` / `SessionReady` |
| 三 | 音频轮次控制 + 打断 | `audio.py` | `Interrupted`（PTT `input.activity_*` 为空载信号） |
| 四 | 字幕 + 工具活动 | `transcript.py` | `Transcript` / `ToolActivity` / `TranscriptRole` / `ToolPhase` |
| 五 | 摄像头单帧往返 | `frame.py` | `FrameRequest` / `FrameResponse` |
| 六 | 控制面杂项 | `control.py` | `TextInput` / `ErrorEvent` |
| 七 | 坐姿守护事件 | `posture.py` | `PostureAlert` |
| 八 | 视觉工具结果 | `vision.py` | `VisionKind` / `Verdict`(三值) / `LookAtPageResult` / `CheckDraftResult` / `ObserveResult` |
| 九 | 天气工具 I/O | `weather.py` | `WeatherGetArgs` / `WeatherResult` |
| 十 | 工具注册表 | `tools.py` | `ToolName` / `ToolSpec` / `TOOL_REGISTRY` / `MODE_TOOLSETS` |
| 十一 | 错误降级 | `errors.py` | `Degradation`(RETRY/FALLBACK_TEXT/FALLBACK_DATA/ABORT) |
| 十二 | MOCK 开关 | `mock.py` + `.env.example` | `KNOWN_MOCKS`(LIVE/VISION/WEATHER) / `is_mock()` |
| 十三 | config.push 下发 | `config_push.py` | `ConfigPushPayload`(posture+voice 子树) |
| 十四 | 配置与密钥 | `config_schema.py` + `config.yaml` + `.env.example` | `ENV_KEYS` / `REQUIRED_CONFIG_SECTIONS` / `load_config()` |

## WS 协议：type → channel → payload 模型（信封类，客户端 ⇄ 后端）

| MessageType | 方向 | channel | payload 模型 |
|---|---|---|---|
| `session.start` | C→S | session | `session.SessionStart` |
| `session.update` | C→S | session | `session.SessionUpdate` |
| `session.ready` | S→C | session | `session.SessionReady` |
| `input.activity_start` | C→S | audio | `{}`（空载，PTT 按下信号） |
| `input.activity_end` | C→S | audio | `{}`（空载，PTT 松手信号） |
| `interrupted` | S→C | audio | `audio.Interrupted` |
| `transcript` | S→C | transcript | `transcript.Transcript` |
| `tool.activity` | S→C | transcript | `transcript.ToolActivity` |
| `frame.request` | S→C | frame | `frame.FrameRequest` |
| `frame.response` | C→S | frame | `frame.FrameResponse` |
| `posture.alert` | C→S | posture | `posture.PostureAlert`（`{severity, ts, reminder_count?}`；`reminder_count` 透传，计数真源在客户端 client_state，后端缝「第 N 次」） |
| `config.push` | S→C | control | `config_push.ConfigPushPayload`（建连即发） |
| `text.input` | C→S | control | `control.TextInput` |
| `error` | S→C | control | `control.ErrorEvent` |

> 音频本体（PCM16 上行 / PCM24 下行）走 **WS 二进制帧**，裸字节、**不裹信封**（B 内部传输）。
> 文本帧恒为信封、二进制帧恒为音频——无帧类型歧义。

## 工具 function_call / response（后端内部，不经客户端 WS）

| 工具 | args | result（function_response） | mock |
|---|---|---|---|
| `look_at_page` | (无入参，即刻抓帧) | `vision.LookAtPageResult{text, confidence}` | MOCK_VISION |
| `check_draft` | (无入参，即刻抓帧) | `vision.CheckDraftResult{verdict, error_line?, confidence}` | MOCK_VISION |
| `observe` | `{hint?}` | `vision.ObserveResult{description, confidence}` | MOCK_VISION |
| `weather_get` | `weather.WeatherGetArgs{lat?, lon?}` | `weather.WeatherResult{temp, precip, ...}` | MOCK_WEATHER |

> 客户端只通过 `tool.activity` 感知「工具在动」，看不到工具内部结果。视觉需要帧 → 经
> `frame.request/response` 向客户端要当前帧（PRD §4.1 按需抓帧）。

## 确定性三落点 → 契约 / config 映射（PRD §1 / §4.1 / §5）

| 落点 | 承载 | 契约 / config 键 |
|---|---|---|
| ① 工具执行体（绿层） | 抓几帧 / 失败回落 / confidence / 视觉预算 | 八·九·十 + `session.vision_budget_per_problem` / `vision_retry_max` |
| ② 客户端状态/闸门（蓝层） | active_problem / reminder_count / gap 判定 / mic gate | 四·七·十三 + `posture.*` / `voice.*`（经 config.push 下发） |
| ③ 提示词约束（server/skills） | 措辞 / 升级语气 / 择时软上界 / 低置信怎么说 | 系统提示 profile（无 config 阈值，不进 prompt） |

> 坐姿三条（PRD §3.2.2）：检测端侧（D）、放行+gap 闸门客户端（mode==learning 或 active_problem!=null）、
> 措辞与最终择时交 Live 模型（proactive）。`posture.alert` 不进工具表（push，非模型能拉的 function call）。
