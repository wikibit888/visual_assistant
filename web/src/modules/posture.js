// Visual Assistant v0.1 · D 姿态守护（纯端侧，PRD §3.2 / §3.2.2 / §4.2）。
//
// 职责：端侧 MediaPipe Pose 双条件检测（颈/背夹角 + 头部前伸）→ 持续 hunchback_hold_ms →
//       客户端放行门控（mode==learning 或 active_problem!=null）→ gap 静默闸门 → 发 posture.alert。
// 边界（铁律）：D 的唯一出口 = posture.alert；绝不出声、绝不入 agent loop、不进工具表。
//       检测端侧、放行客户端（client_state）、措辞与最终择时交模型（proactive）。
//       所有阈值（hunchback_hold_ms / thoracic_kyphosis_deg / head_forward_ratio /
//       gap_min_silence_ms / reminder_cooldown_ms）由 client_state 从 config.push 读，本文件零魔数。
//
// 确定性三处（PRD §3.2.2）：① 检测=本模块；② 放行+择时闸门=client_state；③ 措辞=模型。
// 时序契约：双条件同时满足且持续 ≥ hold → 候选；候选时若(放行 && 不在冷却 && 在 gap)→ 发 alert，
//           reminder_count++（client_state.noteReminderSent）。一次驼背周期只发到松开为止由冷却约束。
//
// ⚠ M0 瘦骨架：MediaPipe Pose 模型推理是「桩 + 清晰接口 + TODO」。CDN 动态 import 真实模型见 TODO[M2]。
//   桩处不抛未捕获异常拖垮页面；导演触发钩子 forceCandidate() 供 demo/联调（PRD §5「demo 用导演触发」）。

import { buildEnvelope, MessageType, Channel } from "./protocol.js";

export class Posture {
  /**
   * @param {WebSocket} ws       已连单 WS（出口只发 posture.alert）
   * @param {ClientState} state  客户端确定性状态（读放行/冷却/gap、写 reminder_count）
   */
  constructor(ws, state) {
    this.ws = ws;
    this.state = state;

    // —— 从 config.push 读阈值（缺键告警，不臆造）——
    this._holdMs = undefined; // posture.hunchback_hold_ms（持续阈，默认 config=30000）
    this._kyphosisDeg = undefined; // posture.thoracic_kyphosis_deg（条件一阈）
    this._headForwardRatio = undefined; // posture.head_forward_ratio（条件二阈）

    this._video = null; // 摄像头 <video>（与 C 抓帧 / voice 共用同源 stream）
    this._pose = null; // MediaPipe Pose 实例（桩；TODO[M2] CDN 动态 import）
    this._running = false;
    this._badSince = 0; // 双条件首次同时满足的时刻（0=当前不满足）
    this._rafId = 0; // 检测循环句柄（桩用 setInterval）
  }

  /** 装配：读阈值 + 取 <video> + 启动检测循环。main.js 在拿到 config + stream 后调一次。 */
  init() {
    const cfg = this.state._cfg || {};
    const p = cfg.posture || {};
    this._holdMs = this._req(p, "hunchback_hold_ms");
    this._kyphosisDeg = this._req(p, "thoracic_kyphosis_deg");
    this._headForwardRatio = this._req(p, "head_forward_ratio");

    this._video = document.getElementById("camera");
    if (!this._video) {
      console.warn("[posture] 未找到 #camera <video>，姿态检测不启动");
      return;
    }

    // TODO[M2]：运行时从 CDN 动态 import MediaPipe Pose（@mediapipe/tasks-vision），
    //   建 PoseLandmarker，喂 this._video 帧 → 关键点 → _evaluatePose()。
    //   import("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision/...") —— 不打包，运行时拉。
    this._startLoop();
    console.debug("[posture] init", {
      holdMs: this._holdMs,
      kyphosisDeg: this._kyphosisDeg,
      headForwardRatio: this._headForwardRatio,
    });
  }

  _req(obj, key) {
    if (obj == null || obj[key] === undefined) {
      console.warn(`[posture] config.push 缺键 posture.${key}（不臆造默认，暴露契约缺漏）`);
      return undefined;
    }
    return obj[key];
  }

  // ── 检测循环（桩：定时器；真实=每视频帧喂 Pose）──────────────────────
  _startLoop() {
    if (this._running) return;
    this._running = true;
    // 桩：周期性「评估」（真实实现是 Pose 回调里评估每帧关键点）。
    // 频率不构成阈值魔数（仅采样节奏）；持续判定用 config 的 hold 阈，与采样频率解耦。
    this._rafId = setInterval(() => this._tick(), 500);
  }

  stop() {
    this._running = false;
    if (this._rafId) clearInterval(this._rafId);
    this._rafId = 0;
    // TODO[M2]：close MediaPipe PoseLandmarker。
  }

  _tick() {
    // TODO[M2]：从 Pose 关键点算「颈/背夹角」「头部前伸比」两个度量，调 _evaluatePose(metrics)。
    // 桩：无真实关键点 → 恒为「坐姿正常」（演示/联调用 forceCandidate() 导演触发）。
    this._evaluatePose({ kyphosisDeg: 0, headForwardRatio: 0 });
  }

  /**
   * 双条件 + 持续阈值判定（PRD §3.2：颈/背夹角 + 头部前伸，持续 hold）。
   * @param {object} m {kyphosisDeg, headForwardRatio} 端侧算出的两度量
   */
  _evaluatePose(m) {
    if (this._kyphosisDeg === undefined || this._headForwardRatio === undefined) return; // 缺阈值不判（已告警）
    // 双条件：两者同时超阈才算驼背候选（防低头写字误判，PRD §3.2 / §11）。
    const bad = m.kyphosisDeg >= this._kyphosisDeg && m.headForwardRatio >= this._headForwardRatio;
    const now = Date.now();

    if (!bad) {
      this._badSince = 0; // 任一条件不满足 → 复位持续计时
      this._setIndicator("ok");
      return;
    }
    if (this._badSince === 0) this._badSince = now; // 双条件首次同时满足
    if (this._holdMs === undefined) return; // 缺持续阈不触发（已告警）
    if (now - this._badSince >= this._holdMs) {
      this._onHunchbackCandidate(now); // 持续达阈 → 候选，进放行/择时闸门
    }
  }

  /**
   * 驼背候选已确认（持续达阈）。过「放行门控 + 冷却 + gap 闸门」才真发 alert（PRD §3.2.2 / §4.2）。
   * 这一层是客户端确定性（蓝层）：挡误触/抢话，模型只决定怎么说、何时说出来。
   */
  _onHunchbackCandidate(now) {
    // ① 放行门控：mode==learning 或 active_problem!=null（client_state 裁）。
    if (!this.state.isPostureReleased()) {
      return; // 未放行：非学习且无活跃题 → 不打扰（PRD §3.2.2）
    }
    // ② 冷却：同类提醒 reminder_cooldown_ms 内不重复（PRD §3.2）。
    if (this.state.isReminderInCooldown(now)) return;
    // ③ gap 闸门：会话静默 ≥ gap_min_silence_ms 才注入（PRD §4.2 缝隙择时）。
    if (!this.state.isInGap(now)) return;

    // 过三闸 → 发 D 的唯一出口 posture.alert（单级、不带话术；contracts/posture.py）。
    this._send(MessageType.POSTURE_ALERT, Channel.POSTURE, {
      severity: "hunchback", // v0.1 单级（contracts/posture.py）
      ts: now,
    });
    this.state.noteReminderSent(now); // reminder_count++ + 记冷却起点（次数在客户端，措辞在模型）
    this._setIndicator("alert");
    // 发出后复位持续计时：避免同一驼背周期内每个 tick 重发（冷却已兜，但提前复位更稳）。
    this._badSince = 0;
  }

  _setIndicator(stateName) {
    // 坐姿指示器仅 learning 显示（ui.applyModeUI 控显隐）；非 learning 时 ui 内部会忽略。
    if (this._ui && this._ui.setPostureState) this._ui.setPostureState(stateName);
  }

  /** main.js 注入 ui 引用，让指示器随检测更新（保持 D 不直接渲染、只调 ui 接口）。 */
  attachUI(ui) {
    this._ui = ui;
  }

  // ── 导演触发钩子（PRD §5「demo 用导演触发」；联调/彩排手动制造一次驼背候选）──
  forceCandidate() {
    this._onHunchbackCandidate(Date.now());
  }

  _send(type, channel, payload) {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(buildEnvelope(type, channel, payload)));
  }
}
