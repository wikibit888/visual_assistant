# TASKS.md · M1–M6 任务卡（拆自 PRD §10；2026-06「基座→收窄」重排）

> 每卡工时 ≤2h；「MOCK 并行」= 置该 `MOCK_X=1` 即可脱真实依赖、与他人并行开发。
> 止损线/硬门见 PRD §10。**契约号见 `contracts/CONTRACTS.md`。** M0 已交付（契约+骨架+fixtures）。

---

## M1 · 语音链路（PRD 2–12h）｜止损：4h 裸链路不通→全程对讲机；7h 自激压不住→锁半双工+对讲机；12h 硬墙

| ID | 任务 | 负责模块 | 工时 | 依赖契约 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|---|---|
| M1-01 | FastAPI 单 WS 装配 + 信封路由 | A/基础 | ✅ | 一 | 可（全 MOCK） | web↔server 信封双向贯通；按 `channel` 分发 |
| M1-02 | 编排器骨架：loop+dispatch 空跑 | A | ✅ | 八/十 | `MOCK_PLANNER`+`MOCK_VISION` | asr.final→planner(脚本)→tts.say 空循环贯通，受 max_tool_rounds 约束 |
| M1-03 | 确定性护栏骨架（置信门控/loop/澄清/视觉预算/粘滞） | A | ✅ | 三/八 | 可 | 各护栏触发有单测；护栏在循环外、不可被覆盖 |
| M1-04 | 状态机 + 间隙仲裁（gap.open 广播 + 姿态放行门控） | A | ✅ | 四 | 可 | IDLE≥2s 开窗 1s（读 config）；放行门控 `learning∨active_problem` 单测 |
| M1-05 | 前端 VAD/PTT/播放队列/**半双工 gate**（真机） | B | ✅ | 二/四 | 否（真机） | 对讲机按键说/打断；AI_SPEAKING 暂停采音、无自激 |
| M1-06 | 后端流式 ASR → asr.final | B | ✅ | 二 | `MOCK_ASR` | 真机出 asr.final（带 confidence）；MOCK 出固定文本 |
| M1-07 | 后端按句 TTS（首句先播）+ stop 语义 | B | ✅ | 二 | `MOCK_TTS` | 按句播；stop=立即停+清队列+回 tts.ack |
| M1-08 | C 视觉 read_problem（gemini 多模态 / MOCK 读 fixture） | C | ✅ | 三 | `MOCK_VISION` | 返回合规 ReadProblemResult；MOCK 读 fixtures |
| M1-09 | D 姿态双条件检测 → posture.alert（端侧） | D | ✅ | 四/§3.2.2 | 端侧独立（零云） | 双条件持续 `hunchback_hold_ms` 才发；低头写字不误触；只发 alert 不出声 |
| M1-10 | E planner system prompt + 引导话术（结构化约束） | E | ✅ | 八 | `MOCK_LLM` | planner 输出符合 PlannerOutput schema；工具白名单生效；E 不内嵌路由 |

---

## M2 · 开放对话基座（PRD 12–18h）｜基座 = 三支柱复用核心（PRD §2/§5.1）｜止损：基座 loop 3 次干跑不稳→准备切 rails

| ID | 任务 | 负责模块 | 工时 | 依赖契约 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|---|---|
| M2-01 | 编排循环接真 planner（deepseek 温度0+结构化）+ 快路径（原 M2-01） | A | ✅ | 八 | `MOCK_VISION` 仍可 | 文本回合零工具快路径；超时 800ms 维持现场景 |
| M2-02 | 开放对话（全交 LLM + 诚实兜底 + 优雅收口）（原 M3-04） | A/E | ✅ | §5.4 | `MOCK_LLM` | 看不清/帮不上即明说；不落死分支；越界→「帮不上」 |
| M2-03 | 工作记忆运行时 + memory_note/recall（原 M2-05） | A | ✅ | 十 | 进程内 | WM 读写正确；会话结束丢弃、绝不落盘 |
| M2-04 | C observe 实现（穿搭/物体）+ observe fixture（原 M3-01；**基座多模态，生活被砍仍保留**） | C | ✅ | 三 | `MOCK_VISION` | 返回合规 ObserveResult；fixture 入库 |
| M2-05 | E 开放对话 prompt + 口头小结 prompt（原 M3-06 开放/小结部分） | E | ✅ | 八 | `MOCK_LLM` | 期望管理话术坦诚；小结含做了几道+坐姿提醒几次 |
| M2-06 | 工具序一致性初测（原 M2-08 初测部分） | E | 0.5h | 八 | `MOCK_LLM` | 同动线工具调用序列一致（非逐字一致） |

> 硬门：开放对话基座跑通——接任意请求、不编造、不落死分支、loop 稳。**agentic 硬门**：基座循环 3 次干跑不稳→准备切 rails。

---

## M3 · 学习收窄（PRD 18–24h）｜P0 demo 锚｜硬门：循环 3 次干跑不稳→切 rails

| ID | 任务 | 负责模块 | 工时 | 依赖契约 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|---|---|
| M3-01 | 学习主路径联调（识题→帮解→批改）+ 置信门控（原 M2-02） | A/C | 2h | 三/八 | 先 MOCK 后真 | mock→真工具跑通；低置信→不报错改请念该行 |
| M3-02 | C check_draft 四值 verdict 实现（原 M2-03） | C | 2h | 三 | `MOCK_VISION` | 四值各样例正确；found_error 必带 error_line/type |
| M3-03 | 坐姿放行并入 active_problem（根因解耦）（原 M2-04） | A | 1h | 四/§3.2.2 | 可 | mode 抖动不吞 alert 的单测通过 |
| M3-04 | 坐姿导演触发联调（强 learning 信号→驼背→间隙提醒）（原 M3-05） | A/D | 1.5h | 四 | 部分 | 导演触发稳定演一次；放行不被 mode 抖动吞 |
| M3-05 | 答案护栏（可选，默认关）正则组合拦截骨架（原 M2-07） | A/E | 1.5h | 九 | 可 | 开启时命中替换追问；循环外；生活语境数字不误杀（基础） |
| M3-06 | rails 切换接线（注入 forced_tool_sequence + max_tool_rounds→0，先锁学习工具序）（原 M2-06） | A | 1.5h | 八 | 可 | config 切 rails：注入工具序+answer 节点，agent 只填语言 |
| M3-07 | E 学习/引导 prompt 调参 + 护栏测试（原 M2-08 学习部分） | E | 1.5h | 八 | `MOCK_LLM` | 引导话术得当；护栏测试通过 |

---

## M4 · 生活收窄（PRD 24–27h）｜唯一牺牲位｜落后 >2h（M3 结束判定）→整段砍（observe 已在基座 M2、开放对话不受损）

| ID | 任务 | 负责模块 | 工时 | 依赖契约 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|---|---|
| M4-01 | weather_get（Open-Meteo+缓存+写死兜底）+ 自动定位回落上海（原 M3-02） | A/工具 | 2h | weather/七 | `MOCK_WEATHER` | 真实+断网兜底+定位失败静默回落，绝不阻塞 |
| M4-02 | 生活编排（observe+weather→行动建议）（原 M3-03） | A | 1.5h | 八 | 可 | 穿搭结论含可执行动作；默认不念具体城市/温度 |
| M4-03 | E 穿搭 prompt（原 M3-06 穿搭部分） | E | 1h | 八 | `MOCK_LLM` | 穿搭话术给到可执行动作 |

---

## M5 · 加固（PRD 27–31h）｜验收：任一降级路径可演；rails 彩排过一次

> = 原 M4，内容不变，仅里程碑号 M4→M5、任务 ID M4-0x→M5-0x。

| ID | 任务 | 负责模块 | 工时 | 依赖契约 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|---|---|
| M5-01 | rails 切换全链路彩排（原 M4-01） | A | 1.5h | 八 | 可 | rails 路径可连贯演一次 |
| M5-02 | 对讲机/半双工/自由对话切换 + 翻车切回（原 M4-02） | B | 2h | 二/四 | 否（真机） | 三态切换顺滑；半双工下无自激；自由对话作高光 |
| M5-03 | 口述批改降级 + 断网天气兜底 + TTS 失败回退字幕（原 M4-03） | A/B/C | 2h | 五 | 可 | 各降级路径可演 |
| M5-04 | 答案护栏误杀专测（含生活语境数字：温度/年龄/楼层）（原 M4-04） | A/E | 1.5h | 九 | 可 | 误杀专测全过；纯字符串无延迟 |
| M5-05 | loop/失控专测 + 视觉预算触顶 FALLBACK_TEXT（原 M4-05） | A | 1.5h | 八 | 可 | 触顶不超调、不超 loop 上限 |
| M5-06 | 开放兜底专测（越界/低置信/双意图诱导）（原 M4-06） | A/E | 1.5h | §5.2 | `MOCK_LLM` | 各边界诚实兜底；防误锁 learning |
| M5-07 | planner 软超时实测校准（真机 deepseek 延迟 >800ms → 重定 `planner_timeout_ms` 或改超时语义） | A | 1h | 八 | 否（真机） | 实测 deepseek p50/p95；调 config 一处使真 planner 不被频繁误兜底；记录依据 |

> **M5-07 缘由（2026-06-14 真机冒烟）**：`scripts/smoke_real_planner.py` 实测真 deepseek planner 每条均 >800ms，
> 现 `config.roles.planner.planner_timeout_ms=800`（PRD §7.2）会让真 planner 几乎每回合触发「维持现场景」兜底、形同失效。
> 需实测延迟后重定阈值（或把软超时改为「流式首 token」语义）；改 config 一处即可，动 PRD §7.2 口径需决策人确认。

---

## M6 · 彩排（PRD 31–36h）

> = 原 M5，内容不变。PRD §10/§11：彩排×3、非标准正确解法专测、模糊草稿验 unreadable、坐姿导演触发（验放行不被吞 + 30s 阈值下低头写字不误触）、开放对话陌生题/物体 ≥3 + 双意图诱导 + 防误锁 learning、噪声一轮、工具序一致性专测等，本表不展开。
