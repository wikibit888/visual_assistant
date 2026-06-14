// Visual Assistant v0.1 · B 语音 I/O（前端侧，PRD §3 / §4.3 / §4.4 / §5）。
//
// 职责：① 采音（PCM16 @in_sample_rate 上行，裸二进制帧）；② 播放下行音频（PCM16 @out_sample_rate 队列）；
//       ③ 对讲机 PTT ↔ 自由对话 VAD 两模式切换；④ 半双工 mic gate（消自激）；
//       ⑤ barge-in：收 interrupted → 立即停播 + 清播放环形缓冲 + 清主线程待发队列。
// 边界：跨进程只走协议——上行二进制音频帧 + input.activity_start/end 控制信封；其余不碰。
//       VAD/打断/轮次判定都在 Live 模型（后端侧）；本模块不自己判轮次（PRD §4.4「塌进模型」）。
//       阈值/采样率由 init(cfg) 从 config.push 下发的 cfg 读，本文件不带魔数。
//
// 时序契约：
//   PTT（PRD §4.3）：pointerdown → 停播+清队列+开 mic+开始采麦 → 发 input.activity_start；
//                    pointerup → 停止采麦 → 发 input.activity_end（触发回答的唯一动作）。
//   自由对话（PRD §4.4）：建连即持续采麦上送；轮次/打断由模型原生 VAD 判，前端只采+播+收 interrupted。
//   半双工 gate（PRD §5）：自由对话/坐姿播报期 gate 麦克风（micGated=true 时静默上行）。
//
// 实现要点：
//   - 采集：AudioContext({sampleRate: in_sample_rate}) 让浏览器把麦重采样到 16k（免手写降采样）；
//     MediaStreamSource → AudioWorkletNode('pcm-capture')，worklet 量化+分块 postMessage(Int16Array.buffer)。
//   - 播放：AudioContext({sampleRate: out_sample_rate}) + AudioWorkletNode('pcm-playback') 环形缓冲，
//     边收边播；收 interrupted 清缓冲；drained 后冷却解 gate。
//   - 降级：getUserMedia/AudioWorklet 不可用 → console 告警 + 优雅降级，不抛未捕获异常拖垮页面。

import { buildEnvelope, MessageType, Channel, VoiceMode } from "./protocol.js";

// addModule 路径相对 index.html（web/index.html）：worklet 在 web/src/worklets/ 下。
const CAPTURE_WORKLET_URL = "./src/worklets/pcm-capture-processor.js";
const PLAYBACK_WORKLET_URL = "./src/worklets/pcm-playback-processor.js";

// 采集分块时长：每块约 N ms 上送（折中延迟 vs 帧数）。非阈值/非契约值，是传输实现常量；
// 真实采样数 = round(in_sample_rate * 此值 / 1000)，采样率仍从 cfg 读（不硬编码采样率）。
const CAPTURE_BLOCK_MS = 20; // TODO[真机标定]：20ms ≈ Live 期望帧粒度；如需更低延迟可调小

// 下行播放环形缓冲容量（秒）：Gemini「尽快、非实时」突发整段 TTS，远快于 24k 实时消费速率；缓冲须
// 能装下整段回答，否则溢出丢最旧 → 回答被切碎/跳音（实测「杂音」主因）。30s 远超单轮长度，内存代价
// 微小（24000×30×4B≈2.9MB）。非业务阈值，是缓冲实现常量（同 CAPTURE_BLOCK_MS，不进 config）。
const PLAYBACK_BUFFER_SECONDS = 30;
// 起播预缓冲（ms）：缓冲首次填充期先攒够这点再开播，吸收流式块间抖动，免开头欠载补零的咔哒声。
// worklet 带等待上限兜底，短回答攒不够也会及时开播（不卡死）。非业务阈值，是去抖实现常量。
const PLAYBACK_PREBUFFER_MS = 80;

export class Voice {
  /**
   * @param {WebSocket} ws       已连的单 WS（上行音频/控制都走它）
   * @param {ClientState} state  客户端确定性状态（读 mic gate / half-duplex / 写下行音频时刻）
   */
  constructor(ws, state) {
    this.ws = ws;
    this.state = state;
    this.voiceMode = VoiceMode.PTT; // 由 setVoiceMode() 更新；开场默认对讲机

    // 采样率：从 config.push 下发的 cfg.audio 读（禁硬编码）；缺则告警并保持 0（init 守卫不建链）。
    this.inSampleRate = 0;
    this.outSampleRate = 0;

    // —— 采集链 ——
    this._captureCtx = null; // 采集 AudioContext（sampleRate=in_sample_rate）
    this._captureNode = null; // AudioWorkletNode('pcm-capture')
    this._captureSrc = null; // MediaStreamSource
    this._captureStream = null; // getUserMedia 的 MediaStream（main/ui 传入复用）
    this._captureReady = false; // 采集链是否已建好（addModule 完成）
    this._capturing = false; // 逻辑上是否「应在采麦」（PTT 按下 / free 常驻）
    // PTT 物理按住中：按住 = 用户接管，压制半双工下行 gate，避免在途下行块把 PTT 上行二次掐断
    // （PRD §4.3 PTT 语义 = 按下即接管打断；半双工 gate 主治自由对话自激，不该反噬 PTT 按住期）。
    this._pttHolding = false;
    this._captureBuildPromise = null; // 防重入：建链中再次调用复用同一 promise

    // —— 播放链 ——
    this._playCtx = null; // 播放 AudioContext（sampleRate=out_sample_rate）
    this._playNode = null; // AudioWorkletNode('pcm-playback')
    this._playReady = false; // 播放链是否已建好
    this._pendingDownlink = []; // 播放链就绪前到达的下行块暂存（就绪后回灌）
    this._playBuildPromise = null;
    this._draining = false; // 是否有「待解 gate」的冷却在进行（防重复定时器）

    // 半双工 gate 解锁冷却（drained 后再等一拍解 gate，避免尾音被采回自激；PRD §5）。
    // 非业务阈值，是去抖实现常量；可标定。
    this._gateReleaseCooldownMs = 150; // TODO[真机标定]：尾音冷却

    // 播放「真 drained」去抖静默窗（ms）：worklet 须连续空 ≥ 此时长才判播报结束，吸收流式块间抖动。
    // 单一真源 = config.voice.playback_drain_quiet_ms（config.push 下发）；缺则告警回落 0 → worklet 退回
    // 内建保守默认（见 pcm-playback-processor.js），前端不臆造去抖魔数（契约·配置）。
    this._drainQuietMs = 0;

    this._pttBtn = null;
  }

  /**
   * 装配：绑定 PTT 按钮、记下复用的 mediaStream、从 cfg 读采样率、建播放链（采集链按需建）。
   * main.js 在拿到 stream + cfg 后调一次。建链是异步的，但本方法即时返回（不阻塞 UI）。
   * @param {object} opts {stream?: MediaStream, voiceMode?: string, cfg?: object}
   */
  init(opts = {}) {
    if (opts.stream) this._captureStream = opts.stream;
    if (opts.voiceMode) this.voiceMode = opts.voiceMode;

    // 采样率从 cfg.audio 读（config.push 下发；契约十三）。缺键告警、不臆造采样率魔数。
    const audio = (opts.cfg && opts.cfg.audio) || {};
    this.inSampleRate = this._requireRate(audio, "in_sample_rate");
    this.outSampleRate = this._requireRate(audio, "out_sample_rate");

    // drained 去抖静默窗从 cfg.voice 读（config.push 下发；契约十三）。缺键回落 0，由 worklet 退到内建默认。
    const voiceCfg = (opts.cfg && opts.cfg.voice) || {};
    const dq = voiceCfg.playback_drain_quiet_ms;
    this._drainQuietMs = typeof dq === "number" && dq > 0 ? dq : 0;
    if (!this._drainQuietMs) {
      console.warn(
        "[voice] config.push 缺 voice.playback_drain_quiet_ms（drained 去抖窗单一真源=config.voice）→ worklet 用内建默认",
      );
    }

    this._pttBtn = document.getElementById("ptt-btn");
    if (this._pttBtn) {
      // 用 pointer 事件统一鼠标/触屏；按住=采麦，松手=结束轮次（PRD §4.3）。
      this._pttBtn.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        this._onPttDown();
      });
      // pointerup / pointercancel / leave 都视为松手，避免漏发结束信号卡住轮次。
      const up = (e) => {
        e.preventDefault();
        this._onPttUp();
      };
      this._pttBtn.addEventListener("pointerup", up);
      this._pttBtn.addEventListener("pointercancel", up);
      this._pttBtn.addEventListener("pointerleave", () => {
        if (this._capturing) this._onPttUp();
      });
    }

    // 播放链建连即建（下行可能在 session.ready 后很快到，先备好缓冲免丢首块）。
    this._ensurePlaybackChain().catch((e) =>
      console.warn("[voice] 播放链建立失败（降级：下行音频不出声，字幕兜底仍在）", e && e.message),
    );

    // 自由对话模式：建连即持续采麦（PRD §4.4）。PTT 模式则等按下。
    if (this.voiceMode === VoiceMode.FREE) this._startContinuousCapture();

    console.debug("[voice] init", {
      voiceMode: this.voiceMode,
      inSampleRate: this.inSampleRate,
      outSampleRate: this.outSampleRate,
    });
  }

  // 采样率缺键 → 告警并返回 0（暴露 config 契约缺漏，不臆造采样率；建链守卫见各 _ensure*）。
  _requireRate(audio, key) {
    const v = audio && audio[key];
    if (typeof v !== "number" || v <= 0) {
      console.warn(
        `[voice] config.push 缺 audio.${key}（采样率单一真源=config.session，禁前端臆造）`,
      );
      return 0;
    }
    return v;
  }

  /** 切换语音模式（UI 切 → main 通知 voice）。ptt↔free 改采麦策略。 */
  setVoiceMode(voiceMode) {
    if (voiceMode === this.voiceMode) return;
    this.voiceMode = voiceMode;
    if (voiceMode === VoiceMode.FREE) {
      this._startContinuousCapture(); // 免按：持续采麦
    } else {
      this._stopCapture(); // 回 PTT：停采，等按下
    }
    console.debug("[voice] setVoiceMode", voiceMode);
  }

  // ── PTT 时序（PRD §4.3）────────────────────────────────────────────────
  _onPttDown() {
    if (this.voiceMode !== VoiceMode.PTT) return;
    // ① 按下瞬间：接管（压制下行 gate）+ 停播 + 清队列（防自己刚说的被采回）+ 开始采麦。
    this._pttHolding = true; // 先置真：后续在途下行块到达不得再翻回 micGated（见 enqueueDownlink）
    this._stopPlaybackAndClear();
    this.state.setMicGated(false); // PTT 采播天然错开，采麦时显式开 mic
    this._syncGateToWorklet(); // 立即把开麦同步给 worklet（解静默）
    this._startCapture();
    // 发轮次开始边界（PRD §4.3：按钮物理状态明确界定轮次）。
    this._send(MessageType.INPUT_ACTIVITY_START, Channel.AUDIO, {});
  }

  _onPttUp() {
    if (this.voiceMode !== VoiceMode.PTT) return;
    this._pttHolding = false; // 松手：交还接管，半双工下行 gate 恢复正常职责
    if (!this._capturing) return;
    // ② 松手：停止采麦 + 发轮次结束信号（触发回答的唯一动作）。
    this._stopCapture();
    this._send(MessageType.INPUT_ACTIVITY_END, Channel.AUDIO, {});
  }

  // ── 采麦（PCM16 @in_sample_rate 上行二进制帧）────────────────────────────
  _startContinuousCapture() {
    this._startCapture();
  }

  _startCapture() {
    if (this._capturing) return;
    this._capturing = true;
    // 建链（幂等、异步）；建好后据当前 gate 状态决定 worklet 是否静默。
    this._ensureCaptureChain()
      .then(async () => {
        if (!this._capturing) return; // 建链期间可能已松手
        // resume 被浏览器自动暂停的 ctx（autoplay 策略）。await 它真正 running 再放行采集，并打印
        // 最终 state：自由对话于 config.push 回调（非手势帧）建链时，若 ctx 卡 suspended 即在此可见
        // （sticky activation 通常允许后续 resume；卡住则需在用户手势里 prime，见诊断）。
        if (this._captureCtx && this._captureCtx.state === "suspended") {
          try {
            await this._captureCtx.resume();
          } catch (e) {
            console.warn("[voice] 采集 ctx.resume 被拒（autoplay）", e && e.name);
          }
        }
        if (this._captureCtx) console.debug("[voice] 采集 ctx.state", this._captureCtx.state);
        this._syncGateToWorklet();
      })
      .catch((e) =>
        console.warn("[voice] 采集链建立失败（降级：本轮不采麦，文字输入兜底仍在）", e && e.message),
      );
    console.debug("[voice] 采麦开始");
  }

  _stopCapture() {
    this._pttHolding = false; // 停采即交还 PTT 接管（含切回 PTT/free 切换、pointerleave 等所有停采路径）
    if (!this._capturing) return;
    this._capturing = false;
    // 让 worklet 进入静默（gate=true）即停上送；不销毁链（下次采麦复用，省重建开销）。
    if (this._captureNode) {
      try {
        this._captureNode.port.postMessage({ type: "gate", on: true });
      } catch (_) {
        /* 节点已失效，忽略 */
      }
    }
    console.debug("[voice] 采麦停止");
  }

  /**
   * 把当前「是否该静默」同步给采集 worklet。
   * 静默条件：① 逻辑上未在采麦（PTT 松手）；或 ② 半双工 gate 关麦（播报/坐姿期）。
   */
  _syncGateToWorklet() {
    if (!this._captureNode) return;
    const silenced =
      !this._capturing || (this.state.halfDuplexEnabled && this.state.micGated);
    try {
      this._captureNode.port.postMessage({ type: "gate", on: silenced });
    } catch (_) {
      /* 节点失效，忽略 */
    }
  }

  /** 建立采集链（幂等）：AudioContext(16k) + worklet + MediaStreamSource。无 stream/采样率则不建。 */
  async _ensureCaptureChain() {
    if (this._captureReady) return;
    if (this._captureBuildPromise) return this._captureBuildPromise;

    this._captureBuildPromise = (async () => {
      if (!this._captureStream) {
        console.warn("[voice] 无 mediaStream（授权失败？）→ 不建采集链，文字输入兜底仍在");
        return;
      }
      if (!this.inSampleRate) {
        console.warn("[voice] 无 in_sample_rate → 不建采集链（采样率缺键已告警）");
        return;
      }
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC || !("audioWorklet" in AC.prototype)) {
        console.warn("[voice] AudioWorklet 不可用 → 采麦降级（文字输入兜底仍在）");
        return;
      }
      // sampleRate=in_sample_rate：浏览器把麦克风重采样到 16k，worklet 无需手写降采样。
      const ctx = new AC({ sampleRate: this.inSampleRate });
      await ctx.audioWorklet.addModule(CAPTURE_WORKLET_URL);

      const blockSamples = Math.max(
        1,
        Math.round((this.inSampleRate * CAPTURE_BLOCK_MS) / 1000),
      );
      const node = new AudioWorkletNode(ctx, "pcm-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 0, // 采集节点无输出（不接 destination）
        processorOptions: {
          blockSamples,
          gated: true, // 建好先静默；由 _syncGateToWorklet 据状态解锁（防建链瞬间漏采/误采）
        },
      });
      // worklet → 主线程：每块 Int16Array.buffer（PCM16 LE）→ 经 gate 判定后 ws.send。
      node.port.onmessage = (e) => this._pushUplinkPCM(e.data);

      const src = ctx.createMediaStreamSource(this._captureStream);
      src.connect(node); // 源 → 采集 worklet（无 destination，纯采集）

      this._captureCtx = ctx;
      this._captureNode = node;
      this._captureSrc = src;
      this._captureReady = true;
      console.debug("[voice] 采集链就绪", { sampleRate: this.inSampleRate, blockSamples });
    })().finally(() => {
      this._captureBuildPromise = null;
    });

    return this._captureBuildPromise;
  }

  /**
   * 上行一块 PCM16 裸音频（worklet onmessage 回调）。
   * 半双工 gate（PRD §5）：micGated=true（播报/坐姿期）时丢弃，消自激（worklet 已静默，此处再守一层）。
   * @param {ArrayBuffer} pcm16 16-bit LE PCM 块
   */
  _pushUplinkPCM(pcm16) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    if (!this._capturing) return; // 逻辑停采（松手）后到的尾块丢弃
    // PTT 按住期（_pttHolding）= 用户接管，上行不因半双工 gate 二次丢弃（在途下行块即便翻了
    // micGated，PTT 采到的人声也要送出去；PRD §4.3 按住即说得进）。半双工双保险只对非按住期生效。
    if (!this._pttHolding && this.state.halfDuplexEnabled && this.state.micGated) {
      return; // 半双工闸门关麦期：不上送（PRD §5 自激规避；双保险）
    }
    this.ws.send(pcm16); // 二进制帧 = 音频，裸字节、不裹信封（contracts/envelope.py）
  }

  // ── 下行播放（PCM16 @out_sample_rate 环形缓冲）──────────────────────────
  /** 建立播放链（幂等）：AudioContext(24k) + worklet → destination。无采样率则不建。 */
  async _ensurePlaybackChain() {
    if (this._playReady) return;
    if (this._playBuildPromise) return this._playBuildPromise;

    this._playBuildPromise = (async () => {
      if (!this.outSampleRate) {
        console.warn("[voice] 无 out_sample_rate → 不建播放链（采样率缺键已告警）");
        return;
      }
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC || !("audioWorklet" in AC.prototype)) {
        console.warn("[voice] AudioWorklet 不可用 → 播放降级（字幕兜底仍在）");
        return;
      }
      const ctx = new AC({ sampleRate: this.outSampleRate });
      // 诊断：Chrome 应 honor 24k；若实际采样率被改写则播放会变速/杂音（理论不达，留一行告警便于真机排查）。
      if (ctx.sampleRate !== this.outSampleRate) {
        console.warn("[voice] 播放 ctx 实际采样率", ctx.sampleRate, "≠ 期望", this.outSampleRate);
      }
      await ctx.audioWorklet.addModule(PLAYBACK_WORKLET_URL);

      // 环形缓冲容量：out_sample_rate × 缓冲秒数（须装下整段突发 TTS，免溢出丢最旧 → 切碎/杂音）。
      const ringCapacity = this.outSampleRate * PLAYBACK_BUFFER_SECONDS;
      // 起播预缓冲样本数：out_sample_rate × 预缓冲 ms（吸收块间抖动，免开头欠载咔哒；worklet 带上限兜底）。
      const prebufferSamples = Math.round((this.outSampleRate * PLAYBACK_PREBUFFER_MS) / 1000);
      const node = new AudioWorkletNode(ctx, "pcm-playback", {
        numberOfInputs: 0,
        numberOfOutputs: 1,
        outputChannelCount: [1], // 单声道源
        // drainQuietMs：drained 去抖静默窗（来自 config.voice，主线程下发）。worklet 据自身渲染量子长度
        // 与 sampleRate 把它换算成「须连续空 M 个量子」，避免单量子瞬时空缓冲误判播报结束（防句中误解 gate）。
        processorOptions: { ringCapacity, drainQuietMs: this._drainQuietMs, prebufferSamples },
      });
      // worklet → 主线程：drained（缓冲拉空）→ 冷却后解半双工 mic gate（PRD §5）。
      node.port.onmessage = (e) => {
        const m = e.data;
        if (m && m.type === "drained") this._onPlaybackDrained();
      };
      node.connect(ctx.destination); // 播放节点 → 扬声器

      this._playCtx = ctx;
      this._playNode = node;
      this._playReady = true;
      console.debug("[voice] 播放链就绪", { sampleRate: this.outSampleRate, ringCapacity });

      // 回灌就绪前暂存的下行块（首块先播，按到达顺序）。
      const pending = this._pendingDownlink;
      this._pendingDownlink = [];
      for (const buf of pending) this._feedPlayback(buf);
    })().finally(() => {
      this._playBuildPromise = null;
    });

    return this._playBuildPromise;
  }

  /**
   * 收到一块下行音频（main.js 在 ws.onmessage 二进制分支里调用）。
   * 记录「最近下行音频时刻」（gap 判定）；播报期 gate 麦克风（PRD §5）；喂播放 worklet。
   * @param {ArrayBuffer} pcm16 PCM16 LE @out_sample_rate
   */
  enqueueDownlink(pcm16) {
    this.state.noteDownlinkAudio(Date.now()); // gap 判定：刚有下行音频 = 非缝隙
    // PTT 按住期不让在途下行块翻回 micGated：否则 _onPttDown 刚开的麦会被下一块在途下行二次掐回，
    // PTT 上行被静默（用户按住却说不进，PRD §4.3）。按住期由 _pttHolding 压制下行 gate。
    //
    // 自由对话（FREE）不硬 gate 采集：硬 gate 会掐死 barge-in（AI 一开口就关麦，用户无法打断），
    // 与 PRD §4.4「自由对话 = 原生 VAD + 可打断」冲突。FREE 下靠 getUserMedia 内建 AEC（已开
    // echoCancellation）消自激、靠 Live 原生 VAD + interrupted 走打断，保持全双工。半双工硬 gate
    // 只在 PTT 播报期生效（PTT 本就非全双工）。[决策权衡：PRD §4.4 全双工 ↔ §5 半双工消自激；
    // 若真机出现自激回授，回退 = 去掉下面的 mode 判定即恢复 FREE 硬 gate]
    if (
      this.state.halfDuplexEnabled &&
      !this._pttHolding &&
      this.voiceMode !== VoiceMode.FREE
    ) {
      this.state.setMicGated(true); // 播报期关麦（消自激）
      this._syncGateToWorklet(); // 立刻让采集 worklet 静默
    }
    this._draining = false; // 新下行到达 → 取消「待解 gate」冷却（仍在播报）

    if (!this._playReady) {
      // 播放链未就绪：先暂存（就绪后按序回灌），并触发建链（首块先播，不丢）。
      this._pendingDownlink.push(pcm16);
      this._ensurePlaybackChain().catch(() => {});
      return;
    }
    this._feedPlayback(pcm16);
  }

  // PCM16 LE → Float32[-1,1] → postMessage 给播放 worklet 环形缓冲。
  _feedPlayback(pcm16) {
    if (!this._playNode) return;
    // resume 被自动暂停的播放 ctx（首次出声需用户手势——进入主界面的点击即手势）。
    if (this._playCtx && this._playCtx.state === "suspended") {
      this._playCtx.resume().catch(() => {});
    }
    const view = new DataView(pcm16);
    const n = pcm16.byteLength >> 1; // 16-bit = 2 字节/样本
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const s = view.getInt16(i * 2, true); // 小端有符号 16-bit
      f32[i] = s < 0 ? s / 32768 : s / 32767; // 负用 32768、正用 32767（对称还原）
    }
    try {
      this._playNode.port.postMessage({ samples: f32 }, [f32.buffer]);
    } catch (e) {
      console.warn("[voice] 喂播放 worklet 失败", e && e.message);
    }
  }

  // 播放缓冲拉空（worklet drained）：冷却一拍后解半双工 mic gate（避免尾音被采回自激；PRD §5）。
  _onPlaybackDrained() {
    if (!this.state.halfDuplexEnabled) return;
    if (this._draining) return; // 已有冷却在跑
    this._draining = true;
    setTimeout(() => {
      // 冷却期内若又来下行（_draining 被 enqueueDownlink 置 false）则不解（仍在播报）。
      if (!this._draining) return;
      this._draining = false;
      this.state.setMicGated(false); // 播报结束 → 恢复采麦
      this._syncGateToWorklet(); // 据当前是否在采麦解 worklet 静默
    }, this._gateReleaseCooldownMs);
  }

  // ── barge-in / 停播（收 interrupted）──────────────────────────────────
  /** 收到后端 interrupted：立即停播 + 清环形缓冲 + 清待发队列（PRD §4.4 / §5 处理中被打断）。 */
  onInterrupted(reason) {
    console.debug("[voice] interrupted", reason);
    this._stopPlaybackAndClear();
  }

  _stopPlaybackAndClear() {
    this._pendingDownlink.length = 0; // 清主线程待灌队列
    if (this._playNode) {
      try {
        this._playNode.port.postMessage({ type: "clear" }); // 清 worklet 环形缓冲（停声）
      } catch (_) {
        /* 节点失效，忽略 */
      }
    }
    this._draining = false;
    if (this.state.halfDuplexEnabled) {
      this.state.setMicGated(false); // 停播即恢复采麦
      this._syncGateToWorklet();
    }
  }

  // ── 内部：发控制信封 ──
  _send(type, channel, payload) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(buildEnvelope(type, channel, payload)));
  }
}
