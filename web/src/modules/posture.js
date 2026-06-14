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
// MediaPipe Pose：运行时从 CDN 动态 import @mediapipe/tasks-vision（不打包），建 PoseLandmarker
//   （VIDEO 模式），用 requestVideoFrameCallback 排帧 + performance.now 软节流喂 detectForVideo，
//   从关键点纯函数算「胸椎后凸角」「头部前伸比」两度量 → _evaluatePose()。任何加载/推理失败都
//   try/catch 兜住：console.warn + this._pose=null，绝不抛未捕获异常拖垮页面/语音链路；导演触发
//   钩子 forceCandidate() 始终可用（PRD §5「demo 用导演触发」）。

import { buildEnvelope, MessageType, Channel } from "./protocol.js";

// ── 运行实现常量（非业务阈值；同 voice.js CAPTURE_BLOCK_MS 先例）──────────────
// 这些是「怎么加载/排帧」的工程常量，不是「判驼背」的业务阈值。业务阈值
//（hunchback_hold_ms / thoracic_kyphosis_deg / head_forward_ratio）一律来自 config.push，本文件零魔数。

// @mediapipe/tasks-vision pin 版的 ESM 入口（vision_bundle.mjs）：固定版本避免 CDN 漂移破坏几何/接口。
const POSE_TASKS_VISION_URL =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";
// FilesetResolver 取 wasm 运行时的根目录（与上面同版本，含 vision_wasm_internal.* ）。
const POSE_WASM_ROOT =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
// Pose Landmarker 轻量模型（端侧、零云调用；lite 足够算肩/耳/鼻几何）。
const POSE_MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";

// 推理软节流间隔（ms）：采样节奏，不是持续判定阈值（hold 阈与采样频率解耦，见 _evaluatePose）。
const DETECT_INTERVAL_MS = 150;
// 两度量一档 EMA 去抖系数（平滑权重，实现常量；越小越稳越钝）。仅平滑噪声，不改判定阈值。
const METRIC_EMA_ALPHA = 0.4;
// 关键点最低可见度（visibility）：低于此视作该点不可信 → 本帧当「坐姿正常」复位（宁漏勿误）。
// MediaPipe visibility ∈ [0,1] 的内部置信度，是「点可不可信」的工程门限，非坐姿业务阈值。
const LANDMARK_MIN_VISIBILITY = 0.5;

// MediaPipe Pose 关键点下标（BlazePose 33 点骨架；固定枚举，非可调值）。
const LM_NOSE = 0;
const LM_LEFT_EAR = 7;
const LM_RIGHT_EAR = 8;
const LM_LEFT_SHOULDER = 11;
const LM_RIGHT_SHOULDER = 12;

export class Posture {
  /**
   * @param {WebSocket} ws       已连单 WS（出口只发 posture.alert）
   * @param {ClientState} state  客户端确定性状态（读放行/冷却/gap、写 reminder_count）
   */
  constructor(ws, state) {
    this.ws = ws;
    this.state = state;

    // —— 从 config.push 读阈值（缺键告警，不臆造）——
    this._holdMs = undefined; // posture.hunchback_hold_ms（持续阈，值来自 config.push）
    this._kyphosisDeg = undefined; // posture.thoracic_kyphosis_deg（条件一阈）
    this._headForwardRatio = undefined; // posture.head_forward_ratio（条件二阈）

    this._video = null; // 摄像头 <video>（与 C 抓帧 / voice 共用同源 stream）
    this._pose = null; // MediaPipe PoseLandmarker 实例（CDN 动态 import；失败=null，导演触发仍可用）
    this._running = false;
    this._badSince = 0; // 双条件首次同时满足的时刻（0=当前不满足）
    this._rafId = 0; // requestVideoFrameCallback 句柄
    this._afId = 0; // requestAnimationFrame 回退句柄
    this._lastDetectAt = 0; // 上次推理时刻（performance.now()，软节流用）
    // 两度量的 EMA 平滑态（null=未起步，首帧直接采用，不引入冷启动偏差）。
    this._kyphosisEma = null;
    this._headForwardEma = null;
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

    // 异步备 PoseLandmarker（不 await：建模型可能慢，先把检测循环挂起来；this._pose 备好前每帧守卫跳过）。
    this._ensurePoseLandmarker();
    this._startLoop();
    console.debug("[posture] init", {
      holdMs: this._holdMs,
      kyphosisDeg: this._kyphosisDeg,
      headForwardRatio: this._headForwardRatio,
    });
  }

  /**
   * 运行时从 CDN 动态 import @mediapipe/tasks-vision 建 PoseLandmarker（VIDEO 模式，端侧）。
   * 全程 try/catch：任何失败 → console.warn + this._pose=null + return（页面/语音链路不崩；
   * forceCandidate 导演触发仍可用）。不打包，运行时拉 pin 版（POSE_*_URL）。
   */
  async _ensurePoseLandmarker() {
    if (this._pose) return;
    try {
      const { FilesetResolver, PoseLandmarker } = await import(POSE_TASKS_VISION_URL);
      const fs = await FilesetResolver.forVisionTasks(POSE_WASM_ROOT);
      const pose = await PoseLandmarker.createFromOptions(fs, {
        baseOptions: { modelAssetPath: POSE_MODEL_URL, delegate: "GPU" },
        runningMode: "VIDEO",
        numPoses: 1,
      });
      if (!this._running) {
        // init 后到模型就绪前已被 stop()：别遗留实例（避免泄漏 + 误触后续帧）。
        try {
          pose.close();
        } catch (_) {
          /* 忽略 */
        }
        return;
      }
      this._pose = pose;
      console.debug("[posture] PoseLandmarker 就绪（端侧 MediaPipe）");
    } catch (e) {
      // CDN/wasm/模型任一加载失败：清晰告警、不崩、退回「无检测」（导演触发 forceCandidate 仍可用）。
      this._pose = null;
      console.warn(
        "[posture] MediaPipe PoseLandmarker 加载失败 → 端侧坐姿检测停用（forceCandidate 导演触发仍可用）",
        e && e.message,
      );
    }
  }

  _req(obj, key) {
    if (obj == null || obj[key] === undefined) {
      console.warn(`[posture] config.push 缺键 posture.${key}（不臆造默认，暴露契约缺漏）`);
      return undefined;
    }
    return obj[key];
  }

  // ── 检测循环（每视频帧喂 Pose；软节流到 DETECT_INTERVAL_MS）──────────────────
  _startLoop() {
    if (this._running) return;
    this._running = true;
    this._scheduleNextFrame();
  }

  // 优先 video.requestVideoFrameCallback（与解码帧对齐、省空转）；不支持回退 requestAnimationFrame。
  _scheduleNextFrame() {
    if (!this._running) return;
    const v = this._video;
    if (v && typeof v.requestVideoFrameCallback === "function") {
      this._rafId = v.requestVideoFrameCallback(() => this._onVideoFrame());
    } else {
      this._afId = requestAnimationFrame(() => this._onVideoFrame());
    }
  }

  stop() {
    this._running = false;
    // 取消帧回调（rVFC 句柄须用 video.cancelVideoFrameCallback；rAF 用 cancelAnimationFrame）。
    const v = this._video;
    if (this._rafId && v && typeof v.cancelVideoFrameCallback === "function") {
      v.cancelVideoFrameCallback(this._rafId);
    }
    if (this._afId) cancelAnimationFrame(this._afId);
    this._rafId = 0;
    this._afId = 0;
    if (this._pose) {
      try {
        this._pose.close();
      } catch (_) {
        /* 忽略 */
      }
    }
    this._pose = null;
  }

  // 每帧回调：软节流到 DETECT_INTERVAL_MS → 推理 → 评估 → 排下一帧（始终续帧，节流只跳过推理）。
  _onVideoFrame() {
    if (!this._running) return;
    const now = performance.now();
    if (now - this._lastDetectAt >= DETECT_INTERVAL_MS) {
      this._lastDetectAt = now;
      this._detectOnce();
    }
    this._scheduleNextFrame();
  }

  // 单次推理：守卫齐备 → detectForVideo → 取 landmarks[0] → 算两度量 → _evaluatePose。
  // 无 landmark / 关键点可见度不足 → 当「坐姿正常」复位（宁漏勿误，PRD §3.2）。
  _detectOnce() {
    const v = this._video;
    if (!this._pose || !v || v.readyState < 2 || !v.videoWidth) return;

    let res;
    try {
      res = this._pose.detectForVideo(v, performance.now());
    } catch (e) {
      // 单帧推理偶发异常不该拖垮循环：告警并当本帧「坐姿正常」复位。
      console.warn("[posture] detectForVideo 异常（本帧跳过）", e && e.message);
      this._evaluatePose({ kyphosisDeg: 0, headForwardRatio: 0 });
      return;
    }

    const lm = res && res.landmarks && res.landmarks[0];
    if (!lm || !this._keyLandmarksVisible(lm)) {
      // 无人/关键点遮挡 → 复位 EMA + 当坐姿正常（避免缺数据被误判持续驼背）。
      this._kyphosisEma = null;
      this._headForwardEma = null;
      this._evaluatePose({ kyphosisDeg: 0, headForwardRatio: 0 });
      return;
    }

    const rawKyphosis = this._computeKyphosisDeg(lm);
    const rawHeadForward = this._computeHeadForwardRatio(lm);
    // 各加一档 EMA 去抖（首帧直接采用，之后指数平滑），喂业务判定。
    this._kyphosisEma = this._ema(this._kyphosisEma, rawKyphosis);
    this._headForwardEma = this._ema(this._headForwardEma, rawHeadForward);
    this._evaluatePose({
      kyphosisDeg: this._kyphosisEma,
      headForwardRatio: this._headForwardEma,
    });
  }

  // 关键点可见度守卫：肩/耳/鼻任一不可信即视作不可用（visibility 是 MediaPipe 工程置信度，非业务阈值）。
  _keyLandmarksVisible(lm) {
    const need = [LM_NOSE, LM_LEFT_EAR, LM_RIGHT_EAR, LM_LEFT_SHOULDER, LM_RIGHT_SHOULDER];
    for (const i of need) {
      const p = lm[i];
      // visibility 可能缺省（部分模型不给）：缺省视为可用，只在显式给出且偏低时判不可信。
      if (!p) return false;
      if (typeof p.visibility === "number" && p.visibility < LANDMARK_MIN_VISIBILITY) {
        return false;
      }
    }
    return true;
  }

  // ── 几何度量（纯函数；归一化坐标 ∈[0,1]，y 轴朝下）──────────────────────────
  /**
   * 胸椎后凸近似：耳中点→肩中点向量与竖直方向的夹角（度）。
   * 坐直时耳在肩正上方（向量近竖直，角小）；驼背/前倾时耳前移（角变大）。
   * @param {Array} lm landmarks[0]（归一化坐标，y 向下）
   * @returns {number} 夹角度数（≥0）
   */
  _computeKyphosisDeg(lm) {
    const shoulder = this._mid(lm[LM_LEFT_SHOULDER], lm[LM_RIGHT_SHOULDER]);
    const ear = this._mid(lm[LM_LEFT_EAR], lm[LM_RIGHT_EAR]);
    // 肩→耳向量（耳在上方时 y 更小）。竖直方向 = y 轴；与竖直的夹角 = atan2(|dx|, |dy|)。
    const dx = ear.x - shoulder.x;
    const dy = ear.y - shoulder.y;
    const rad = Math.atan2(Math.abs(dx), Math.abs(dy)); // 与竖直轴的偏角（dy=0 时 →90°）
    return (rad * 180) / Math.PI;
  }

  /**
   * 头部前伸比：鼻相对肩中点的水平前探量，按肩宽归一（消远近/分辨率差异）。
   * @param {Array} lm landmarks[0]
   * @returns {number} dx/肩宽（≥0）
   */
  _computeHeadForwardRatio(lm) {
    const ls = lm[LM_LEFT_SHOULDER];
    const rs = lm[LM_RIGHT_SHOULDER];
    const shoulderMidX = (ls.x + rs.x) / 2;
    const dx = Math.abs(lm[LM_NOSE].x - shoulderMidX);
    const shoulderWidth = Math.abs(ls.x - rs.x);
    const eps = 1e-6; // 除零保护（实现常量，非业务阈值）
    return dx / Math.max(shoulderWidth, eps);
  }

  _mid(a, b) {
    return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  // 一档 EMA：prev 为 null（冷启动/复位后首帧）直接采用 next，避免冷启动偏差。
  _ema(prev, next) {
    if (prev === null || prev === undefined) return next;
    return METRIC_EMA_ALPHA * next + (1 - METRIC_EMA_ALPHA) * prev;
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

    // 先记次数再发（时序铁律）：reminder_count 是客户端确定性真源，必须在发出**之前** ++ 并读出
    // 带进 payload；否则首条发出时 count 仍是旧值（陈旧 0），后端「第 N 次」从第一条就错位。
    this.state.noteReminderSent(now); // reminder_count++ + 记冷却起点（次数在客户端，措辞在模型）
    // 过三闸 → 发 D 的唯一出口 posture.alert（单级、不带话术；contracts/posture.py）。
    this._send(MessageType.POSTURE_ALERT, Channel.POSTURE, {
      severity: "hunchback", // v0.1 单级（contracts/posture.py）
      ts: now,
      reminder_count: this.state.reminderCount, // 本会话累计第几次（后端透传缝进「第 N 次」措辞）
    });
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
