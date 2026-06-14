// Visual Assistant v0.1 · B 语音 I/O（前端侧，PRD §3 / §4.3 / §4.4 / §5）。
//
// 职责：① 采音（PCM16 @16k 上行，裸二进制帧）；② 播放下行音频（PCM24 @24k 队列）；
//       ③ 对讲机 PTT ↔ 自由对话 VAD 两模式切换；④ 半双工 mic gate（消自激）；
//       ⑤ barge-in：收 interrupted → 立即停播 + 清播放队列。
// 边界：跨进程只走协议——上行二进制音频帧 + input.activity_start/end 控制信封；其余不碰。
//       VAD/打断/轮次判定都在 Live 模型（后端侧）；本模块不自己判轮次（PRD §4.4「塌进模型」）。
//       阈值（barge_in_min_ms 等）由 client_state 从 config.push 读，本文件不带魔数。
//
// 时序契约：
//   PTT（PRD §4.3）：pointerdown → 停播+清队列+关 mic gate+开始采麦 → 发 input.activity_start；
//                    pointerup → 停止采麦 → 发 input.activity_end（触发回答的唯一动作）。
//   自由对话（PRD §4.4）：建连即持续采麦上送；轮次/打断由模型原生 VAD 判，前端只采+播+收 interrupted。
//   半双工 gate（PRD §5）：自由对话/坐姿播报期 gate 麦克风（micGated=true 时丢弃上行音频）。
//
// ⚠ M0 瘦骨架：AudioWorklet 的 PCM16 采集 / PCM24 播放底层是「桩 + 清晰接口 + TODO」，
//   不抛未捕获异常拖垮页面。真实音频往返见各 TODO[M2-Live]。

import { buildEnvelope, MessageType, Channel, VoiceMode } from "./protocol.js";

// 采样率：上行恒 16k（PRD §3）；下行 24k。优先用 config.session 下发的值，缺则用常量并 TODO。
// 注：config.push 只下发 posture/voice 两子树（contracts/config_push.py），session 子树暂不在内，
//     故此处先用常量并注明 TODO 由后端下发（server/main.py 可扩 config.push 带 session.audio_*）。
const FALLBACK_IN_SAMPLE_RATE = 16000; // TODO[契约]：改由 config.session.audio_in_sample_rate 下发
const FALLBACK_OUT_SAMPLE_RATE = 24000; // TODO[契约]：改由 config.session.audio_out_sample_rate 下发

export class Voice {
  /**
   * @param {WebSocket} ws       已连的单 WS（上行音频/控制都走它）
   * @param {ClientState} state  客户端确定性状态（读 mic gate / half-duplex / 写下行音频时刻）
   */
  constructor(ws, state) {
    this.ws = ws;
    this.state = state;
    this.voiceMode = VoiceMode.PTT; // 由 setVoiceMode() 更新；开场默认对讲机
    this.inSampleRate = FALLBACK_IN_SAMPLE_RATE;
    this.outSampleRate = FALLBACK_OUT_SAMPLE_RATE;

    this._audioCtx = null; // 播放用 AudioContext（桩；TODO 真实建）
    this._captureStream = null; // getUserMedia 的音频轨（由 main/ui 传入复用）
    this._capturing = false; // 是否正在采麦
    this._playQueue = []; // 下行 PCM24 chunk 队列（barge-in 时清空）
    this._playing = false; // 是否正在播放

    this._pttBtn = null;
  }

  /**
   * 装配：绑定 PTT 按钮、记下复用的 mediaStream。main.js 在拿到 stream + config 后调一次。
   * @param {object} opts {stream?: MediaStream, voiceMode?: string}
   */
  init(opts = {}) {
    if (opts.stream) this._captureStream = opts.stream;
    if (opts.voiceMode) this.voiceMode = opts.voiceMode;

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

    // 自由对话模式：建连即持续采麦（PRD §4.4）。PTT 模式则等按下。
    if (this.voiceMode === VoiceMode.FREE) this._startContinuousCapture();

    console.debug("[voice] init", { voiceMode: this.voiceMode });
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
    // ① 按下瞬间：停播 + 清队列（防自己刚说的被采回）+ 开始采麦。
    this._stopPlaybackAndClear();
    this.state.setMicGated(false); // PTT 采播天然错开，采麦时显式开 mic
    this._startCapture();
    // 发轮次开始边界（PRD §4.3：按钮物理状态明确界定轮次）。
    this._send(MessageType.INPUT_ACTIVITY_START, Channel.AUDIO, {});
  }

  _onPttUp() {
    if (this.voiceMode !== VoiceMode.PTT) return;
    if (!this._capturing) return;
    // ② 松手：停止采麦 + 发轮次结束信号（触发回答的唯一动作）。
    this._stopCapture();
    this._send(MessageType.INPUT_ACTIVITY_END, Channel.AUDIO, {});
  }

  // ── 采麦（PCM16 @16k 上行二进制帧）────────────────────────────────────
  _startContinuousCapture() {
    this._startCapture();
  }

  _startCapture() {
    if (this._capturing) return;
    this._capturing = true;
    // TODO[M2-Live]：建 AudioContext(sampleRate=this.inSampleRate) + AudioWorkletNode，
    //   从 worklet onmessage 拿 Float32 → 转 PCM16 LE → 经 _pushUplinkPCM() 上送。
    //   桩：仅记录状态，不真采。真实实现需 web/src/worklets/pcm-capture.js（M2 建）。
    console.debug("[voice] 采麦开始（桩；TODO[M2-Live] AudioWorklet PCM16）");
  }

  _stopCapture() {
    if (!this._capturing) return;
    this._capturing = false;
    // TODO[M2-Live]：断开 worklet / suspend AudioContext。
    console.debug("[voice] 采麦停止");
  }

  /**
   * 上行一块 PCM16 裸音频（真实采集回调里调用）。
   * 半双工 gate（PRD §5）：micGated=true（播报/坐姿期）时丢弃，消自激。
   * @param {ArrayBuffer} pcm16 16-bit LE PCM 块
   */
  _pushUplinkPCM(pcm16) {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    if (this.state.halfDuplexEnabled && this.state.micGated) {
      return; // 半双工闸门关麦期：不上送（PRD §5 自激规避）
    }
    this.ws.send(pcm16); // 二进制帧 = 音频，裸字节、不裹信封（contracts/envelope.py）
  }

  // ── 下行播放（PCM24 @24k 队列）─────────────────────────────────────────
  /**
   * 收到一块下行音频（main.js 在 ws.onmessage 二进制分支里调用）。
   * 入队 + 记录「最近下行音频时刻」（client_state gap 判定用）。
   * 半双工：播报期 gate 麦克风（PRD §5）。
   * @param {ArrayBuffer} pcm24 24-bit/16-bit LE PCM @24k（桩播放器不深究位深）
   */
  enqueueDownlink(pcm24) {
    this.state.noteDownlinkAudio(Date.now()); // gap 判定：刚有下行音频 = 非缝隙
    this._playQueue.push(pcm24);
    if (this.state.halfDuplexEnabled) this.state.setMicGated(true); // 播报期关麦
    this._drainPlayQueue();
  }

  _drainPlayQueue() {
    if (this._playing) return;
    if (this._playQueue.length === 0) {
      // 队列空 = 播报结束：开 mic gate（半双工恢复采麦）。
      if (this.state.halfDuplexEnabled) this.state.setMicGated(false);
      return;
    }
    this._playing = true;
    const chunk = this._playQueue.shift();
    // TODO[M2-Live]：把 PCM24 LE → AudioBuffer（this.outSampleRate）→ AudioBufferSourceNode 播放，
    //   onended 里 this._playing=false 后递归 _drainPlayQueue() 接下一块（无缝队列）。
    //   桩：立即「播完」推进队列，不真出声（旧前端 SpeechSynthesis 假播放已弃，PRD 下行是真 PCM24）。
    console.debug("[voice] 播放 chunk（桩；TODO[M2-Live] PCM24→AudioBuffer）", chunk.byteLength);
    this._playing = false;
    this._drainPlayQueue();
  }

  // ── barge-in / 停播（收 interrupted）──────────────────────────────────
  /** 收到后端 interrupted：立即停播 + 清队列（PRD §4.4 / §5 处理中被打断）。 */
  onInterrupted(reason) {
    console.debug("[voice] interrupted", reason);
    this._stopPlaybackAndClear();
  }

  _stopPlaybackAndClear() {
    this._playQueue.length = 0; // 清播放队列
    this._playing = false;
    // TODO[M2-Live]：stop 当前 AudioBufferSourceNode（真实停声）。桩仅清队列。
    if (this.state.halfDuplexEnabled) this.state.setMicGated(false); // 停播即恢复采麦
  }

  // ── 内部：发控制信封 ──
  _send(type, channel, payload) {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(buildEnvelope(type, channel, payload)));
  }
}
