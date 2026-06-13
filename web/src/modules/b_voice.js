// 模块 B（前端半）· 语音 I/O（PRD §7.1 / §7.5 / §7.7 契约二·四）。M1-05 真机实现。
// 职责：VAD（vad-web）/ 对讲机 PTT / 播放队列 / 半双工 gate。
// - 开场默认对讲机（cfg.turn_state.default_voice_mode=ptt）；自由对话(VAD)作高光。
// - AI_SPEAKING 期间半双工 gate 麦克风（暂停采音 → 消灭自激链；PRD §7.5 第一技术风险）。
// - 上行：每个用户回合 finalize 一段音频交 M1-06 后端 ASR（asr.final 经后端产，前端不臆造）。
// - 下行：tts.say（入队按句播）/ tts.stop（立即停 + 清队列 + 回 tts.ack）。
// 数值（vad_speaking_min_ms 等）一律从 cfg.turn_state 读，前端不硬编码阈值（契约七 / CLAUDE.md §4）。
//
// 边界（铁律 §2）：跨模块只走信封（契约一 Envelope）。本模块只收 tts.say/tts.stop、只发 tts.ack；
//   gap.open / posture.alert / turn_id 分配不属本模块（A/D 负责）。
//
// 时序契约（M1-01b 定稿）：main.js 在「建连→config.push→缓存 cfg」之后才调 initVoice(ws, cfg)，
//   被调用时 cfg 必为非 null 对象；故此处直接读 cfg，缺键只告警不兜魔数。

// vad-web = PRD/CLAUDE 钉死的 VAD 选型；原生 ESM 无打包 → 运行时按需从 CDN 动态 import。
// 仅在切到自由对话(free)时才加载；默认对讲机(ptt)零外部依赖、可离线跑（PRD 止损线：对讲机优先调稳）。
const VAD_ESM_URL = "https://esm.sh/@ricky0123/vad-web@0.0.22";

// 信封 schema 版本 = 协议常量（对齐 contracts.envelope.SCHEMA_VERSION）；非可调阈值，故写在码内。
const SCHEMA_VERSION = "0.1";

export function initVoice(ws, cfg) {
  // ── 1. 从 cfg 读阈值（禁硬编码；缺键告警，不静默兜默认值，以暴露上游契约缺漏）──
  const T = (cfg && cfg.turn_state) || {};
  const need = [
    "default_voice_mode",
    "vad_speaking_min_ms",
    "half_duplex_gate",
    "ptt_gap_after_tts_ms",
  ];
  for (const k of need) {
    if (T[k] === undefined) console.warn(`[b_voice] cfg.turn_state.${k} 缺失（config.push 未下发？）`);
  }
  const MODE_DEFAULT = T.default_voice_mode; // "ptt" | "free"（开场默认对讲机）
  const VAD_MIN_MS = T.vad_speaking_min_ms; // 自由对话连续人声 ≥ 此值才判「说话/打断」
  const HALF_DUPLEX = T.half_duplex_gate; // AI_SPEAKING 期间半双工 gate 麦克风
  const GAP_AFTER_TTS_MS = T.ptt_gap_after_tts_ms; // TTS 结束后再开麦的冷却（对讲机间隙判定 + 防尾音回灌）

  // ── 2. DOM（仅 querySelector 既有占位节点，禁改 index.html；M1-01b 已放 id）──
  const pttBtn = document.querySelector("#ptt-btn");
  const subtitlesEl = document.querySelector("#subtitles"); // 字幕兜底区（含 aria-live）
  const statusEl = document.querySelector("#status");
  if (pttBtn) pttBtn.style.touchAction = "none"; // 长按不触发滚动/选择（触屏 PTT）

  // ── 3. 状态机（前端只持有 IO 面三态；PLANNING_ACTING/GAP 属 A）──
  const State = { IDLE: "idle", LISTENING: "listening", AI_SPEAKING: "ai_speaking" };
  let state = State.IDLE;
  let mode = MODE_DEFAULT;

  let micStream = null; // getUserMedia 音轨（带内建 AEC）；半双工 gate 用 track.enabled 控制
  let recorder = null; // 对讲机段录制器；松键 flush 出整段
  let recChunks = [];
  let vad = null; // vad-web MicVAD 实例（自由对话用）
  let vadSpeechTimer = null; // vad 起始去抖：连续 ≥ VAD_MIN_MS 才确认说话
  let vadLoading = false;

  const playQueue = []; // 下行 TTS 播放队列（按到达＝按句 seq 顺序；首句先播）
  let speaking = false; // 是否正在播一句
  let rearmTimer = null; // AI_SPEAKING 结束后的开麦冷却定时器
  let utteranceCount = 0; // M1-06 接：已 finalize 的用户回合数（真机自检：开麦计数增，gate 期不增）

  // ── 4. 信封工具（跨模块只走信封）──
  const send = (type, channel, turn_id, payload) =>
    ws.send(JSON.stringify({ type, ts: Date.now(), turn_id, channel, payload, schema_version: SCHEMA_VERSION }));

  // ── 5. UI 反馈 ──
  const caption = (text) => {
    if (subtitlesEl) subtitlesEl.textContent = text; // aria-live=polite → 朗读给屏阅
  };
  const renderStatus = () => {
    if (!statusEl) return;
    const tag = mode === "free" ? "自由对话" : "对讲机";
    const s =
      state === State.AI_SPEAKING
        ? `🔊 AI 说话中 · 半双工 gate ${HALF_DUPLEX ? "ON" : "OFF"}`
        : state === State.LISTENING
        ? "🎤 采音中…"
        : `🎙 就绪 · ${tag}`;
    statusEl.textContent = s;
  };

  // ── 6. 半双工 gate：on=true → 暂停采音（消灭自激）。track.enabled=false 让麦克风对所有消费者出静音 ──
  const gateMic = (on) => {
    if (!HALF_DUPLEX) return; // 开关在 config，关则不 gate（代价＝可能自激，仅调试用）
    if (micStream) micStream.getAudioTracks().forEach((t) => (t.enabled = !on));
    if (vad && on) vad.pause(); // 自由对话：暂停 VAD，AI 自己的声音不会触发打断（PRD：演打断用对讲机）
  };

  // ── 7. 麦克风采集（对讲机路径）──
  async function ensureMic() {
    if (micStream) return micStream;
    // 内建 AEC = 自激第一道防线（CLAUDE.md §1 / PRD §7.5）；阈值类参数无，故仅开启浏览器原生处理。
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    return micStream;
  }

  function startCapture() {
    if (recorder && recorder.state === "recording") return;
    const tracks = micStream ? micStream.getAudioTracks() : [];
    tracks.forEach((t) => (t.enabled = true)); // 解 gate
    recChunks = [];
    try {
      recorder = new MediaRecorder(micStream);
      recorder.ondataavailable = (e) => e.data && e.data.size && recChunks.push(e.data);
      recorder.onstop = flushUtterance;
      recorder.start(); // 不传 timeslice：一段 PTT 出一个整 blob（无周期魔数）
    } catch (e) {
      console.warn("[b_voice] MediaRecorder 不可用：", e.message);
    }
    state = State.LISTENING;
    renderStatus();
  }

  function stopCapture() {
    if (recorder && recorder.state === "recording") recorder.stop(); // → onstop → flushUtterance
    else flushUtterance();
  }

  // 一个用户回合的音频已成段。M1-06 接：经后端流式 ASR → asr.final（带 confidence、turn_id 由 A 分配）。
  // 本里程碑不上行音频：后端 /ws 仅 receive_text，二进制帧/专用 envelope 须 M1-06 配套后端改造；
  // 此处只做 finalize 计数 + 字幕占位，作为 M1-06 的对接缝（保证采集/gate 链路真机可验）。
  function flushUtterance() {
    if (recChunks.length) {
      utteranceCount += 1;
      const bytes = recChunks.reduce((n, b) => n + b.size, 0);
      console.debug(`[b_voice] 回合音频成段 #${utteranceCount}（${bytes}B）→ M1-06 上行 ASR`);
      caption("🎤 已收到你的话（识别中…）"); // asr.final 经后端回来后由 A→字幕更新
    }
    recChunks = [];
    micStream && micStream.getAudioTracks().forEach((t) => (t.enabled = false)); // 回到 idle gate 态
    if (state === State.LISTENING) {
      state = State.IDLE;
      renderStatus();
    }
  }

  // ── 8. 播放队列（下行 TTS）+ 半双工 gate ──
  function enqueueTts(item) {
    if (rearmTimer) {
      clearTimeout(rearmTimer); // 新句来了，取消上一段的开麦冷却
      rearmTimer = null;
    }
    playQueue.push(item); // 首句先播：A 按 seq 递增发送，到达即顺序
    if (!speaking) playNext();
  }

  function playNext() {
    const item = playQueue.shift();
    if (!item) {
      speaking = false;
      rearmAfterSpeaking();
      return;
    }
    speaking = true;
    state = State.AI_SPEAKING;
    gateMic(true); // 进 AI_SPEAKING → 暂停采音（半双工硬 gate）
    renderStatus();
    caption(`AI：${item.text}`);
    speakText(item.text, () => playNext()); // 一句播完接着下一句（保持 AI_SPEAKING）
  }

  // v0.1 播放器 = Web SpeechSynthesis（真机出声 → 自激可被 gate 真实验证）。
  // M1-07 后端云 TTS 落地后可把本函数换成「播放后端下发的按句音频」，队列/gate/stop/ack 契约不变。
  function speakText(text, onDone) {
    const synth = window.speechSynthesis;
    if (!synth || typeof SpeechSynthesisUtterance === "undefined") {
      console.warn("[b_voice] 无 SpeechSynthesis：TTS 回退字幕（PRD §8）");
      Promise.resolve().then(onDone); // 仅字幕，立即收尾（不造时长魔数）
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "zh-CN";
    u.onend = onDone;
    u.onerror = onDone; // 合成失败也收尾，靠字幕兜底
    synth.speak(u);
  }

  // 全句播完 → 退出 AI_SPEAKING：等 ptt_gap_after_tts_ms 冷却再开麦（对讲机间隙判定 + 防 TTS 尾音回灌）。
  function rearmAfterSpeaking() {
    if (rearmTimer) clearTimeout(rearmTimer);
    rearmTimer = setTimeout(() => {
      rearmTimer = null;
      state = State.IDLE;
      if (mode === "free" && vad) vad.start(); // 自由对话：冷却后恢复 VAD 监听
      renderStatus();
    }, GAP_AFTER_TTS_MS);
  }

  // 本地停播（清队列 + 取消合成）。不发 tts.ack（ack 仅用于回应 A 的 tts.stop）。
  function stopPlaybackLocal() {
    playQueue.length = 0;
    speaking = false;
    if (window.speechSynthesis) window.speechSynthesis.cancel();
  }

  // 收到 A 的 tts.stop：立即停 + 清队列 + 回 tts.ack（打断必须可感知地确认；契约二）。
  function handleTtsStop(turn_id) {
    stopPlaybackLocal();
    send("tts.ack", "voice", turn_id, { turn_id, stopped: true }); // 即刻 ack，再走冷却
    if (state === State.AI_SPEAKING) {
      state = State.IDLE;
      renderStatus();
    }
    rearmAfterSpeaking();
  }

  // ── 9. 下行信封监听（自挂监听器：main.js 未路由 tts.*，且禁改 main.js；WS 支持多监听器）──
  ws.addEventListener("message", (ev) => {
    let env;
    try {
      env = JSON.parse(ev.data);
    } catch {
      return; // 非 JSON 帧非本模块所收
    }
    if (env.type === "tts.say") {
      enqueueTts({ text: env.payload?.text ?? "", turn_id: env.turn_id, seq: env.payload?.seq ?? 0 });
    } else if (env.type === "tts.stop") {
      handleTtsStop(env.payload?.turn_id ?? env.turn_id);
    }
    // 其余类型（config.push/gap.open/posture.* 等）非本模块职责，忽略。
  });

  // ── 10. 对讲机 PTT（默认主路径；亦是「演打断」的确定性入口）──
  async function onPttDown(e) {
    e.preventDefault();
    if (pttBtn && e.pointerId !== undefined) {
      try {
        pttBtn.setPointerCapture(e.pointerId); // 指针移出按钮也能收到 up
      } catch {}
    }
    // 打断：AI 说话时按下 = 立即停 AI 本地播放并开麦说新话（PRD §7.5 演打断用对讲机）。
    // 用户发起的打断无 B→A 控制消息（契约无），靠随后 asr.final 的新回合自然取代旧回合。
    if (state === State.AI_SPEAKING || speaking) stopPlaybackLocal();
    if (rearmTimer) {
      clearTimeout(rearmTimer);
      rearmTimer = null;
    }
    try {
      await ensureMic();
    } catch (err) {
      console.warn("[b_voice] 取麦失败：", err.message);
      caption("⚠ 麦克风不可用，请授权");
      return;
    }
    if (pttBtn) pttBtn.textContent = "松开结束";
    startCapture();
  }

  function onPttUp(e) {
    e && e.preventDefault();
    if (pttBtn) pttBtn.textContent = "按住说话";
    if (state === State.LISTENING) stopCapture(); // → flushUtterance → M1-06 上行 ASR
  }

  if (pttBtn) {
    pttBtn.addEventListener("pointerdown", onPttDown);
    pttBtn.addEventListener("pointerup", onPttUp);
    pttBtn.addEventListener("pointercancel", onPttUp);
    pttBtn.addEventListener("lostpointercapture", onPttUp);
  }

  // ── 11. 自由对话（VAD，高光特性）：按需加载 vad-web，失败优雅回退对讲机 ──
  async function enableVad() {
    if (vad || vadLoading) return;
    vadLoading = true;
    try {
      const mod = await import(/* @vite-ignore */ VAD_ESM_URL);
      await ensureMic();
      vad = await mod.MicVAD.new({
        // AEC 走浏览器原生（自激第一防线）；连续人声门限交由下方 JS 去抖按 VAD_MIN_MS 判定。
        additionalAudioConstraints: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        onSpeechStart: () => {
          // 起始去抖：连续说话满 VAD_MIN_MS 才算「真说话」，否则视作误触不开麦。
          if (state === State.AI_SPEAKING) return; // 半双工：AI 说话期不接管（gate 已 pause，此为双保险）
          if (vadSpeechTimer) clearTimeout(vadSpeechTimer);
          vadSpeechTimer = setTimeout(() => {
            vadSpeechTimer = null;
            state = State.LISTENING;
            renderStatus();
          }, VAD_MIN_MS);
        },
        onSpeechEnd: () => {
          if (vadSpeechTimer) {
            clearTimeout(vadSpeechTimer); // 未满 VAD_MIN_MS 就结束 → 误触，丢弃
            vadSpeechTimer = null;
            return;
          }
          if (state === State.LISTENING) {
            utteranceCount += 1; // M1-06 接：onSpeechEnd 的 audio 上行 ASR（同对讲机缝）
            console.debug(`[b_voice] VAD 回合成段 #${utteranceCount} → M1-06 上行 ASR`);
            caption("🎤 已收到你的话（识别中…）");
            state = State.IDLE;
            renderStatus();
          }
        },
      });
      vad.start();
    } catch (e) {
      console.warn("[b_voice] vad-web 加载/启动失败，回退对讲机：", e.message);
      vad = null;
      mode = "ptt"; // 高光翻车即切回对讲机（PRD §10 止损线）
    } finally {
      vadLoading = false;
      renderStatus();
    }
  }

  function disableVad() {
    if (vadSpeechTimer) {
      clearTimeout(vadSpeechTimer);
      vadSpeechTimer = null;
    }
    if (vad) {
      try {
        vad.pause();
      } catch {}
    }
  }

  // ── 12. 模式切换 + 控制句柄（无模式切换 DOM → 经控制句柄/导演 console 切；默认 ptt）──
  async function setMode(next) {
    if (next !== "ptt" && next !== "free") return;
    mode = next;
    if (next === "free") await enableVad();
    else disableVad();
    renderStatus();
  }

  renderStatus();
  if (MODE_DEFAULT === "free") setMode("free"); // 一般为 ptt：开场不取麦/不连 CDN，离线即可演

  const controller = {
    setMode,
    getState: () => ({ state, mode, queued: playQueue.length, speaking, utteranceCount }),
    // 真机自检钩子：可在 console 注入一句 TTS 验「播放队列 + 半双工 gate」（无需后端）。
    _injectSay: (text, turn_id = "t-000000", seq = 0) => enqueueTts({ text, turn_id, seq }),
  };
  // 挂到 main.js 暴露的 window.__va（已存在）；供导演 console 切模式/自检。
  window.__va = window.__va || {};
  window.__va.voice = controller;
  return controller;
}
