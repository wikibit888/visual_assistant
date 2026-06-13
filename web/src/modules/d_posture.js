// 模块 D · 坐姿守护（PRD §3.2 / §3.2.2 / §7.1）。M1-09 实现。
// 100% 端侧（MediaPipe Pose），云成本恒为 0。**只发 posture.alert，绝不出声、绝不入 agent loop。**
// 双条件触发（胸椎后凸角 + 头前伸比），持续 hunchback_hold_ms 才发；低头写字不误触。
// 放行/择机插入由 A 护栏层裁（§3.2.2）；D 不读 planner、不做话术、不带话术文本（单级 severity）。
// 演示口径：§9 由演示者导演触发（主动明显驼背）；window.__va_posture 提供确定性 QA 钩子。
// 阈值（hunchback_hold_ms / reminder_cooldown_ms / thoracic_kyphosis_deg / head_forward_ratio）
// 全部由 cfg.posture 下发，本文件禁硬编码任何姿态阈值（铁律 §4）。

// —— 前端基础设施常量（CDN 端点 / 模型资产 / 管线调参；非契约姿态阈值，姿态阈值一律读 cfg）——
// MediaPipe Tasks-Vision 走原生 ES module 动态 import（项目不打包；CLAUDE.md §1）。
const MP_VISION_ESM =
  "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18/vision_bundle.mjs";
const MP_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.18/wasm";
const POSE_MODEL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";

// BlazePose 33 点索引（仅取可靠上半身点 + 髋部估计点）。
const LM = { NOSE: 0, L_EAR: 7, R_EAR: 8, L_SH: 11, R_SH: 12, L_HIP: 23, R_HIP: 24 };

// 关键点可见度门限（这是 MediaPipe 模型置信度过滤，属管线细节，非姿态契约阈值）。
const VIS_MIN = 0.5; // 肩/耳/鼻：可靠点，低于此判定姿态不可读 → 不计条件、复位计时
const HIP_VIS_MIN = 0.3; // 髋部：坐姿常被桌沿遮挡，软门限；看不到髋则保守不触发后凸条件

// 姿态检测采样间隔（性能调参，非阈值）：~10fps 足够判长持续坐姿，省 CPU/GPU。
const DETECT_INTERVAL_MS = 100;
// 髋部持续不可见的提示节流（仅日志，便于现场调相机取景）。
const HIP_HINT_THROTTLE_MS = 5000;

// —— 几何 helpers（点取自 worldLandmarks 时为米制 3D、髋心为原点；否则归一化图像坐标）——
function visOf(p) {
  return typeof p?.visibility === "number" ? p.visibility : 1;
}
function mid3(a, b) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 };
}
function dist3(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
}
// 顶点 b 处 ∠(a,b,c)，单位度（3D）。
function angleDeg(a, b, c) {
  const v1 = { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
  const v2 = { x: c.x - b.x, y: c.y - b.y, z: c.z - b.z };
  const m1 = Math.hypot(v1.x, v1.y, v1.z);
  const m2 = Math.hypot(v2.x, v2.y, v2.z);
  if (m1 < 1e-6 || m2 < 1e-6) return 180;
  let cos = (v1.x * v2.x + v1.y * v2.y + v1.z * v2.z) / (m1 * m2);
  cos = Math.max(-1, Math.min(1, cos));
  return (Math.acos(cos) * 180) / Math.PI;
}

/**
 * 初始化坐姿守护（端侧）。main.js 在收到 config.push、缓存 cfg 后调用，cfg 必非 null。
 * @param {WebSocket} ws  单总线连接；D 仅经此发 posture.alert，绝不接收驱动行为。
 * @param {{posture:Object}} cfg  后端 config.push 下发的阈值快照。
 */
export function initPosture(ws, cfg) {
  const P = (cfg && cfg.posture) || {};
  const holdMs = P.hunchback_hold_ms;
  const cooldownMs = P.reminder_cooldown_ms;
  const kyphThresholdDeg = P.thoracic_kyphosis_deg;
  const headFwdThreshold = P.head_forward_ratio;

  // 阈值缺一不可（前端不臆造默认值；阈值由后端 config 下发——铁律 §4）。缺则不启动。
  for (const [k, v] of Object.entries({
    hunchback_hold_ms: holdMs,
    reminder_cooldown_ms: cooldownMs,
    thoracic_kyphosis_deg: kyphThresholdDeg,
    head_forward_ratio: headFwdThreshold,
  })) {
    if (typeof v !== "number") {
      console.warn(`[d_posture] cfg.posture.${k} 缺失或非数值，坐姿守护不启动`);
      return null;
    }
  }

  const video = document.querySelector("#camera"); // 只 querySelector，不改 index.html
  if (!video) {
    console.warn("[d_posture] 未找到 #camera，坐姿守护不启动");
    return null;
  }

  // —— 运行状态 ——
  let running = true;
  let landmarker = null;
  let bothSince = null; // 双条件同时满足的起始时刻（performance.now ms）；任一条件断 → 复位
  let cooldownUntil = 0; // 发过 alert 后的冷却截止（performance.now ms）
  let lastDetectAt = 0;
  let lastHipHintAt = 0;
  let simulateBoth = false; // QA 钩子：强制双条件成立，验证 hold→alert 全链路
  const last = { kyph: null, headFwd: null, cond1: false, cond2: false, hipSeen: false };

  // D 唯一出口：posture.alert（契约四 PostureAlert）。单级 severity、不带话术；
  // turn_id 用哨兵 t-000000（端侧无回合上下文，A 在间隙仲裁时按当时回合关联——见 state_machine.py）。
  function sendAlert() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn("[d_posture] ws 未连接，丢弃 posture.alert（可丢弃不排队）");
      return;
    }
    const now = Date.now();
    ws.send(
      JSON.stringify({
        type: "posture.alert",
        ts: now,
        turn_id: "t-000000",
        channel: "posture",
        payload: { severity: "hunchback", ts: now },
        schema_version: "0.1",
      })
    );
    console.debug("[d_posture] ws ↑ posture.alert (hunchback)");
  }

  // 单帧双条件判定。pts 用 worldLandmarks（3D 米制、髋心原点）→ 角度/前伸对正脸亦可测、尺度不变；
  // 可见度恒读图像 landmarks（worldLandmarks 不带 visibility）。
  function evaluate(imgLm, worldLm) {
    if (simulateBoth) {
      last.cond1 = true;
      last.cond2 = true;
      last.hipSeen = true;
      return true;
    }
    if (!imgLm || imgLm.length <= LM.R_HIP) return null;
    const pts = worldLm && worldLm.length > LM.R_HIP ? worldLm : imgLm;

    const lShV = imgLm[LM.L_SH],
      rShV = imgLm[LM.R_SH],
      lEarV = imgLm[LM.L_EAR],
      rEarV = imgLm[LM.R_EAR],
      noseV = imgLm[LM.NOSE];
    // 可靠点不全可见 → 姿态不可读：不计任何条件（evaluate 返回 null 触发复位）。
    if (
      visOf(lShV) < VIS_MIN ||
      visOf(rShV) < VIS_MIN ||
      visOf(lEarV) < VIS_MIN ||
      visOf(rEarV) < VIS_MIN ||
      visOf(noseV) < VIS_MIN
    ) {
      return null;
    }

    const sh = mid3(pts[LM.L_SH], pts[LM.R_SH]);
    const ear = mid3(pts[LM.L_EAR], pts[LM.R_EAR]);
    const nose = pts[LM.NOSE];
    const sw = dist3(pts[LM.L_SH], pts[LM.R_SH]); // 肩宽：尺度归一基准
    if (sw < 1e-3) return null;

    // 条件二 · 头前伸比：头（耳中点）相对肩沿前探的深度，按肩宽归一（尺度/位置不变）。
    // worldLandmarks 中 z 越小越靠近相机 → 头前探则 ear.z < sh.z，差为正。低头写字与驼背都会升高此值。
    const headFwd = (sh.z - ear.z) / sw;

    // 条件一 · 胸椎后凸角：耳-肩-髋屈曲角的补角（直背≈0°，背越圆角越大）。这是区分器——
    // 低头写字（背挺直、仅颈屈）此角≈0 → cond1 不成立 → 不触发；驼背则背圆 → cond1 成立。
    let kyph = null;
    const lHipV = imgLm[LM.L_HIP],
      rHipV = imgLm[LM.R_HIP];
    if (visOf(lHipV) >= HIP_VIS_MIN && visOf(rHipV) >= HIP_VIS_MIN) {
      const hip = mid3(pts[LM.L_HIP], pts[LM.R_HIP]);
      kyph = 180 - angleDeg(ear, sh, hip);
    } else {
      // 看不到髋（桌沿遮挡）→ 无法确认后凸 → 保守不触发，避免低头写字误判（PRD §3.2 优先级）。
      const now = performance.now();
      if (now - lastHipHintAt > HIP_HINT_THROTTLE_MS) {
        lastHipHintAt = now;
        console.info("[d_posture] 髋部不在画面内，后凸条件暂不可测；请让相机取到胸/腰部");
      }
    }

    last.kyph = kyph;
    last.headFwd = headFwd;
    last.hipSeen = kyph != null;
    last.cond1 = kyph != null && kyph >= kyphThresholdDeg; // 后凸（驼背）
    last.cond2 = headFwd >= headFwdThreshold; // 头前伸/低头
    return last.cond1 && last.cond2;
  }

  // 持续阈值 + 冷却：双条件须连续满足 holdMs 才发；发后 cooldownMs 内不再发（同类提醒 60s 冷却）。
  function updateHold(bothNow, nowMs) {
    if (!bothNow) {
      bothSince = null; // 任一条件断开即复位（短时低头/抖动不累积）
      return;
    }
    if (bothSince == null) bothSince = nowMs;
    if (nowMs < cooldownUntil) return; // 冷却期内不发
    if (nowMs - bothSince >= holdMs) {
      sendAlert();
      cooldownUntil = nowMs + cooldownMs;
      bothSince = null; // 复位，下一条 alert 需重新累积满 hold
    }
  }

  // —— 主循环 ——
  async function loop() {
    if (!running) return;
    const now = performance.now();
    if (
      landmarker &&
      now - lastDetectAt >= DETECT_INTERVAL_MS &&
      video.readyState >= 2 &&
      video.videoWidth > 0
    ) {
      lastDetectAt = now;
      try {
        const res = landmarker.detectForVideo(video, now);
        const imgLm = res?.landmarks?.[0] || null;
        const worldLm = res?.worldLandmarks?.[0] || null;
        const both = simulateBoth ? evaluate(null, null) : evaluate(imgLm, worldLm);
        if (both == null) {
          bothSince = null; // 姿态不可读：复位，不累积
        } else {
          updateHold(both, now);
        }
      } catch (e) {
        console.warn("[d_posture] 检测帧失败：", e.message);
      }
    } else if (simulateBoth) {
      updateHold(evaluate(null, null), now); // QA 钩子：无需视频流也能验 hold 链路
    }
    requestAnimationFrame(loop);
  }

  async function ensureCameraStream() {
    if (video.srcObject) return; // #camera 与 C 抓帧共用；若已有流则复用，不抢占
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: false,
      });
      video.srcObject = stream;
      await video.play().catch(() => {});
    } catch (e) {
      console.warn("[d_posture] 取摄像头失败，坐姿守护降级（不影响其余链路）：", e.message);
    }
  }

  async function start() {
    try {
      const { PoseLandmarker, FilesetResolver } = await import(MP_VISION_ESM);
      const fileset = await FilesetResolver.forVisionTasks(MP_WASM);
      try {
        landmarker = await PoseLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: POSE_MODEL, delegate: "GPU" },
          runningMode: "VIDEO",
          numPoses: 1,
        });
      } catch (gpuErr) {
        console.info("[d_posture] GPU 委派不可用，回退 CPU：", gpuErr.message);
        landmarker = await PoseLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: POSE_MODEL, delegate: "CPU" },
          runningMode: "VIDEO",
          numPoses: 1,
        });
      }
      await ensureCameraStream();
      console.debug("[d_posture] MediaPipe Pose 就绪，双条件守护启动（hold=%dms）", holdMs);
    } catch (e) {
      // CDN/WASM/模型不可用：D 降级为不监测（绝不抛断其余链路）；QA 钩子仍可验信封链路。
      console.warn("[d_posture] MediaPipe 初始化失败，坐姿守护降级：", e.message);
    }
    requestAnimationFrame(loop);
  }

  start();

  // 调试 / QA 钩子（仅 d_posture 内挂载）：导演触发为物理驼背；此钩子供确定性验收。
  //   __va_posture.state()       → 当前条件/角度快照
  //   __va_posture.simulate(b)   → 强制/解除双条件成立，验证「持续 holdMs 才发一条 alert」
  //   __va_posture.forceAlert()  → 立即发一条（验信封形态 + 不出声，绕过 hold）
  const handle = {
    stop() {
      running = false;
      landmarker?.close?.();
    },
    state() {
      return { ...last, bothSince, cooldownUntil, simulateBoth, holdMs, cooldownMs };
    },
    simulate(on) {
      simulateBoth = !!on;
      if (!on) bothSince = null;
    },
    forceAlert() {
      sendAlert();
    },
  };
  window.__va_posture = handle;
  return handle;
}
