# 契约索引（M0 钉死 · 对照 PRD §7.7）

> 这是跨模块的**真理来源**。改动任何契约 = 改动基线，需决策人授权 + 同步 fixtures。
> 铁律（PRD §7.1）：跨模块只走总线消息（契约一）；禁互相 import 内部对象；
> `gap.open` 只由 A 广播；`tts.say/stop` 只发 B；D 只输出 `posture.alert`；
> 每个字经 A 的确定性护栏；planner 不得绕过护栏直产 TTS，护栏不得被提示词关闭。

| # | 契约 | PRD 形态 | 落点文件 | 关键符号 |
|---|---|---|---|---|
| 一 | 消息信封 | Pydantic | `envelope.py` + `message_types.py` | `Envelope` / `MessageType` / `Channel` |
| 二 | ASR 出口 / TTS 入口 | 文档 + Pydantic | `voice.py` | `AsrFinal` / `TtsSay` / `TtsStop` / `TtsAck`（stop=停+清队列+ack） |
| 三 | 视觉结果 schema | 文档 + Pydantic | `vision.py` | `VisionKind` / `Verdict`(四值) / `ReadProblemResult` / `CheckDraftResult` / `ObserveResult` |
| 四 | 轮次状态机 + 间隙仲裁 | 文档 | `state_machine.py` | `TurnState`(五态) / `GapOpen` / `PostureAlert`；半双工 gate；放行门控 |
| 五 | 错误降级 | 注释 | `errors.py` | `Degradation`(RETRY/FALLBACK_TEXT/ABORT/TOOL_FAIL) |
| 六 | MOCK 开关 | env | `mock.py` + `.env.example` | `KNOWN_MOCKS` / `is_mock()` |
| 七 | 配置密钥 | 文件 | `config_schema.py` + `config.yaml` + `.env.example` | `ENV_KEYS` / `REQUIRED_CONFIG_SECTIONS` / `load_config()` |
| 八 | 编排循环 + 工具注册表 | Pydantic + 文档 | `orchestration.py` | `PlannerOutput` / `Mode` / `ToolName` / `ToolSpec` / `RailStep` / `TOOL_REGISTRY` |
| 九 | 答案护栏（可选） | 文档 | `answer_guard.py` | `AnswerGuardConfig` / `GuardDecision`（循环外、默认关） |
| 十 | 工作记忆 schema | Pydantic | `working_memory.py` | `WorkingMemory`（仅内存不落盘）/ `MemoryNoteArgs` / `MemoryRecallArgs` |

辅助契约：`weather.py`（weather.get 工具 I/O，挂契约八工具注册表）。

## type → payload 模型映射（信封校验用）

| MessageType | channel | payload 模型 |
|---|---|---|
| `asr.final` | voice | `voice.AsrFinal` |
| `tts.say` | voice | `voice.TtsSay` |
| `tts.stop` | voice | `voice.TtsStop` |
| `tts.ack` | voice | `voice.TtsAck` |
| `vision.request` | vision | `{kind, hint?}` |
| `vision.result` | vision | `vision.ReadProblemResult` / `CheckDraftResult` / `ObserveResult`（按 kind） |
| `weather.request` | weather | `weather.WeatherGetArgs` |
| `weather.result` | weather | `weather.WeatherResult` |
| `posture.alert` | posture | `state_machine.PostureAlert` |
| `gap.open` | orchestrator | `state_machine.GapOpen` |
| `config.push` | control | `config_push.ConfigPushPayload`（A 建连下发 turn_state+posture 子树） |

> 注：PRD §7.7 契约一正文枚举的是**终态**总线消息；`vision.request` / `weather.request`（工具往返的请求侧）是**实现级**信封——工具调用同样只走信封（铁律），不另开内部通道。此为文档↔实现对账闭环，未改任何 PRD 产品决策。
> 注：`posture.alert.turn_id` 由 A 在接收时关联（D 端侧无回合上下文），详见 `state_machine.py:PostureAlert`。

## 护栏 → 契约/config 映射（PRD §7.4）

| 护栏 | 契约 | config 键 |
|---|---|---|
| 置信门控 | 三 | `orchestration.confidence_gate=0.6` |
| 澄清上限 | 八 | `orchestration.clarify_max=1` |
| loop 上限 | 八 | `orchestration.max_tool_rounds=2` |
| 视觉预算 | 八 | `orchestration.vision_budget_per_problem=3` |
| 粘滞兜底 | 八 | `roles.planner.planner_timeout_ms=800` |
| 姿态不抢话 | 四 | `posture.*` + `turn_state.gap_window_ms` |
| 答案护栏（可选） | 九 | `answer_guard.enabled=false` |
