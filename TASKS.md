# TASKS.md · Live 版里程碑（拆自 PRD；2026-06-14「手搓→Live」技术骨干重构后重排）

> **新 M0 已交付**（本轮）：Live 契约（一~十四）+ 后端瘦骨架（中继 relay + 工具执行体 tools +
> 技能 skills + 供应商 llm）+ 前端 shell 骨架（引导页+三模式+语音切换+字幕开关+学习坐姿指示器）+
> config/.env + fixtures + 文档。`uv run pytest -q` 全绿。下面 M1–M6 在此基础上接。

> **三条并行轨**（MOCK 解耦，互不阻塞——PRD §1.5）：
> - **契约轨**：已锁（`contracts/`）。动它 = 动基线，需决策人授权 + 同步 fixtures/测试。
> - **后端轨 server/**：中继接真实 Live 会话 + 工具执行体真路径。`MOCK_LIVE/VISION/WEATHER=1` 可脱云开发。
> - **前端轨 web/**：采播音频 + 端侧姿态 + 客户端状态/闸门 + UI。靠 WS 协议契约对接，后端可全 MOCK。
>
> **第一风险 = 语音链路本身**（PRD §5：强依赖云端、无离线退路）。故 M1 把 Live 真机闭环前置，
> 围绕**开放对话**（基座，PRD §2/§5 动线最完整）先打通。止损：弱网无退路 → 网络预热/保底 +
> 字幕兜底 + 文字输入降级（PRD §5/§8）。

---

## M1 · Live 链路打通（开放对话真机端到端）｜第一风险前置

| ID | 任务 | 轨 / 模块 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|
| M1-01 | `live_bridge` 接真实 Gemini Live 会话：`client.aio.live` 连接 + 注入 `system_for_mode(mode)` profile + `MODE_TOOLSETS` 工具声明 + 回 `session.ready` | 后端 · relay/llm | MOCK_LIVE 仍可 | 真实会话建立；mode profile + 工具子集注入；session.ready 回客户端 |
| M1-02 | 泵 Live 输出 → 客户端（音频 PCM24 下行 / `transcript` / `interrupted`）+ 客户端 PCM16 上行透传进会话 | 后端 · relay | — | 真机音频双向通；字幕流式下发；打断事件下发 |
| M1-03 | `function_call` 派发 → `tool_dispatch` → `function_response`（含视觉预算计数 + `frame.request/response` 抓帧往返） | 后端 · relay/tools | MOCK_VISION 仍可 | `look_at_page` 真帧闭环；单题预算封顶；越预算回「念给我听」 |
| M1-04 | AudioWorklet PCM16 采集 → 二进制帧上行 + 半双工 mic gate（AI_SPEAKING 期暂停采音，消自激） | 前端 · voice | — | 真机采音；gate 期不采；首轮无自激 |
| M1-05 | PCM24 播放队列（边收边播）+ barge-in（收 `interrupted` 立即停播 + 清队列） | 前端 · voice | — | 首句先播；打断立停清队列 |
| M1-06 | 摄像头帧抓取：收 `frame.request` → 抓 `<video>` 当前帧 JPEG base64 → `frame.response`（request_id 配对） | 前端 · voice/ui | — | 真帧回传；配对正确；受视觉预算约束 |
| M1-07 | 开放对话 PTT 端到端真机冒烟 ×≥3（连→session.start→说→看画面→答；字幕显示；首响达标） | 全 · 真机 | 否 | 开放动线连贯演 ≥3；首响 ≤config 目标（填充语盖往返）；半双工无自激 |

> 硬门：**开放对话真机闭环跑通**——对着摄像头+麦克风用语音聊、看不清/帮不上诚实说明、不编造、首响达标、无自激。

---

## M2 · 双语音模式 + 打断高光（PRD §4.3 / §4.4）

| ID | 任务 | 轨 / 模块 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|
| M2-01 | 前端三态：对讲机 PTT（按下/松手发 `input.activity_*` 边界）+ 自由对话（连续流，靠 Live 原生 VAD 判轮次）+ 切换 | 前端 · voice | MOCK_LIVE | PTT 确定性轮次；自由免按；切换顺滑 |
| M2-02 | 后端：PTT 边界 → Live `activityStart/End`；自由模式不发边界（交模型 VAD） | 后端 · relay | — | 对讲机轮次准；自由靠模型断轮次 |
| M2-03 | barge-in 真机（自由对话模型说话时用户开口打断）+ 半双工 gate + 内建 AEC | 真机 · voice/relay | 否 | 打断流畅；半双工无自激；高光可演 / 翻车可切回对讲机 |
| M2-04 | 运行时切换全链路：右上切 `mode`（换 profile + 工具子集）/ 模式内切 `voice_mode` / 字幕开关（`session.update`） | 并行 · ui/relay | MOCK_LIVE | 三类切换即时生效；切 mode 重配会话 |

---

## M3 · 学习收窄（P0 demo 锚）

> **M3-01~05 已实现**（分支 `feat/m3-batch1`；`uv run pytest -q` → 40 绿、前端 `node --check` OK）。
> ✅ = 代码完成；其**真机验收**（绝不报答案 / 逆光→unreadable / 30s 不误报 / 「第N次」/ turn_complete 择时）
> 统一并入 **M3-06 真机+导演触发联调**（PRD §5 视觉/语音链路无 mock 退路）。

| ID | 任务 | 轨 / 模块 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|
| ✅ M3-01 | `check_draft` 真帧批改：三值 `verdict` + `error_line` + `error_type`（只定位错误行，不报答案）。**结构化输出硬约束**（schema 无答案字段）+ server 侧红线校验 | 后端 · tools | MOCK_VISION | 三值各样例对；found_error 必带 error_line |
| ✅ M3-02 | D 端侧 MediaPipe 双条件检测（颈/背夹角 + 头前伸）+ 持续 `hunchback_hold_ms`(30s)。CDN 动态 import；阈值占位待真机标定 | 前端 · posture | — | 双条件持续才触发；低头写字不误触；只发 alert 不出声 |
| ✅ M3-03 | 客户端坐姿放行门控（`mode==learning` 或 `active_problem!=null`）+ gap 闸门（静默≥阈）+ `reminder_count++`（**修时序：先计数再发**） | 前端 · posture/client_state | — | mode 抖动不吞 alert；非缝隙不注入；次数累计 |
| ✅ M3-04 | 后端：`posture.alert` 作为 text 事件注入 Live 会话。**契约加 `reminder_count` 透传**，「第N次」打通；`turn_complete=True`（gap 闸门已保非抢话；proactive 待 M3-06 核验） | 后端 · relay | — | 提醒缝进「第二步/第 3 次」；不打断关键思路 |
| ✅ M3-05 | skills 学习 profile 调参：分步引导阶梯（方向/操作/示范）+ 批改口径（三值+低置信）+ 坐姿措辞（最多等一句） | 并行 · skills | MOCK_LIVE | 引导得当；绝不顺嘴报答案 |
| ⏳ M3-06 | 学习动线真机 + 坐姿导演触发联调（识题→帮解→批改→缝隙提醒） | 全 · 真机 | 否 | 主路径连贯；坐姿提醒不被 mode 抖动吞；30s 下低头写字不误报 |

---

## M4 · 生活收窄（唯一牺牲位）｜落后 / 时间告急 → 整段砍（开放/学习不受损）

| ID | 任务 | 轨 / 模块 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|
| M4-01 | `weather_get` 真 Open-Meteo（httpx）+ 城市/小时缓存 + 断网/定位失败写死兜底（绝不阻塞） | 后端 · tools | MOCK_WEATHER | 真实 + 断网兜底 + 回落上海，任何失败不抛 |
| M4-02 | 前端定位：`navigator.geolocation` → 随 weather 调用注入 lat/lon；拒绝/失败静默 | 前端 · ui/client_state | — | 定位失败不阻塞；回落默认城市 |
| M4-03 | skills 生活 profile：observe + weather 融合 → 具体到行动建议（加外套/带伞），不播报数字 | 并行 · skills | MOCK | 穿搭结论含可执行动作；默认不念城市/温度 |

---

## M5 · 加固（降级路径，PRD §5 / §8）

| ID | 任务 | 轨 / 模块 | MOCK 并行 | 验收标准 |
|---|---|---|---|---|
| M5-01 | Live/TTS 断流 → 字幕兜底 + 文字输入（`text.input` 注入会话）；`error` 事件驱动 UI 降级 | 并行 · relay/ui | — | 各降级路径可演；字幕 + 文字键入可继续 |
| M5-02 | 视觉预算触顶「我已看过，念给我听」+ 低置信诚实兜底（提示词约束）真机验 | 真机 · tools/skills | MOCK_VISION | 触顶不超调；逆光/模糊 → 低置信不编造 |
| M5-03 | 断网天气兜底 + 定位回落 + Live 断线重连预热 | 真机 · tools/relay | MOCK_WEATHER | 弱网不崩；兜底可演 |
| M5-04 | 越界帮不上 + 模式误判自纠 + 防误锁 learning 专测 | 并行 · skills | MOCK_LIVE | 各边界诚实兜底；session memory 自纠 |

---

## M6 · 彩排

> PRD §5：彩排 ×3、开放对话陌生题/物体 ≥3 + 越界请求、学习识题→批改→坐姿导演触发（验放行不被吞 +
> 30s 阈值下低头写字不误报）、生活穿搭一次、双语音模式切换 + 高光翻车切回、噪声一轮、首响达标、
> 半双工无自激、断网/TTS 失败降级各演一次。本表不展开。
