// Visual Assistant v0.1 · UI 渲染层（PRD §2 三支柱 / §4.1 / §4.3-4.4 / 用户前端设计要求）。
//
// 职责：引导页(landing) ↔ 主界面(app) 切换；右上三模式选择器（学习/生活/开放）；
//       模式内语音切换（对讲机 PTT 默认 / 自由对话 VAD）；字幕开关；
//       学习专属坐姿状态指示器；字幕/历史面板渲染；状态/错误条。
// 边界：只碰 DOM + 暴露事件回调给 main.js（onEnter / onModeChange / onVoiceModeChange /
//       onSubtitlesToggle / onTextInput）。不直接发 WS、不持业务状态——把用户意图回调出去，
//       由 main.js 决定发 session.start / session.update。
//
// ── 稳定 DOM id 契约（index.html 实现，ui.js/voice.js/posture.js 共享）──
//   landing 引导页：
//     #landing                  引导页根容器
//     #landing-mode-group       模式三选一（data-mode=open|learning|life 的按钮）
//     #landing-enter            「进入」按钮（先授权摄像头/麦克风再进主界面）
//     #perm-status              授权状态文案
//   app 主界面：
//     #app                      主界面根容器（初始 hidden）
//     #camera                   摄像头预览 <video>（D 姿态 + C 抓帧共用；voice/posture 取它）
//     #frame-canvas             抓帧用离屏 <canvas>（frame.request → JPEG；voice/posture 不用，main 用）
//     #mode-selector            右上模式选择器容器
//     #mode-group               运行时模式三选一（data-mode=...）
//     #voice-mode-toggle        语音模式切换按钮（ptt ↔ free）
//     #subtitles-toggle         字幕开关 <input type=checkbox>
//     #posture-indicator        坐姿状态指示器（仅 learning 显示；data-state=ok|alert|idle）
//     #ptt-btn                  对讲机 PTT 按钮（voice.js 绑 pointerdown/up）
//     #transcript-panel         字幕/历史面板容器（subtitles=true 时显示）
//     #transcript-list          字幕条目列表（ui.appendTranscript 往里塞）
//     #tool-activity            工具活动轻提示（tool.activity → 一句话）
//     #text-input               文字输入兜底 <input>（语音降级）
//     #text-input-send          文字发送按钮
//     #status                   连接状态文案
//     #error-banner             错误/降级提示条（默认 hidden）

import { Mode, VoiceMode } from "./protocol.js";

export class UI {
  /**
   * @param {object} handlers main.js 注入的事件回调（用户意图 → main 决定发什么 WS）：
   *   onEnter({mode, voiceMode, subtitles})  引导页「进入」（授权后）→ main 连 WS + 发 session.start
   *   onModeChange(mode)                     运行时切模式 → main 发 session.update{mode}
   *   onVoiceModeChange(voiceMode)           切语音模式 → main 发 session.update{voice_mode} + 通知 voice
   *   onSubtitlesToggle(bool)                字幕开关 → main 发 session.update{subtitles} + 控面板显隐
   *   onTextInput(text)                      文字兜底 → main 发 text.input
   */
  constructor(handlers = {}) {
    this.h = handlers;
    // 引导页待选状态（进入前的本地选择，进入时一次性带给 main）。
    this._pendingMode = Mode.OPEN; // 交互原型先围绕「开放对话」打磨 → 默认 open
    this._pendingVoiceMode = VoiceMode.PTT; // 开场默认对讲机（PRD §4.3）
    this._pendingSubtitles = true; // 默认显示字幕（动线最完整）
    // 运行时当前态（进入主界面后跟随 session）。
    this._mode = null;
    this._voiceMode = null;
    this._subtitles = true;

    this._el = {}; // 缓存 DOM 引用
  }

  /** 抓取 DOM + 绑定事件。DOMContentLoaded 后由 main.js 调一次。 */
  mount() {
    const $ = (id) => document.getElementById(id);
    this._el = {
      landing: $("landing"),
      landingModeGroup: $("landing-mode-group"),
      landingEnter: $("landing-enter"),
      permStatus: $("perm-status"),
      app: $("app"),
      modeGroup: $("mode-group"),
      voiceToggle: $("voice-mode-toggle"),
      subtitlesToggle: $("subtitles-toggle"),
      postureIndicator: $("posture-indicator"),
      transcriptPanel: $("transcript-panel"),
      transcriptList: $("transcript-list"),
      toolActivity: $("tool-activity"),
      textInput: $("text-input"),
      textSend: $("text-input-send"),
      status: $("status"),
      errorBanner: $("error-banner"),
    };

    this._bindLanding();
    this._bindAppControls();
    this._renderLandingModeSelection();
    return this;
  }

  // ── 引导页 ──────────────────────────────────────────────────────────────
  _bindLanding() {
    const { landingModeGroup, landingEnter } = this._el;
    if (landingModeGroup) {
      landingModeGroup.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-mode]");
        if (!btn) return;
        this._pendingMode = btn.dataset.mode;
        this._renderLandingModeSelection();
      });
    }
    if (landingEnter) {
      landingEnter.addEventListener("click", () => this._onEnterClicked());
    }
  }

  _renderLandingModeSelection() {
    const group = this._el.landingModeGroup;
    if (!group) return;
    group.querySelectorAll("[data-mode]").forEach((b) => {
      b.classList.toggle("selected", b.dataset.mode === this._pendingMode);
      b.setAttribute("aria-pressed", String(b.dataset.mode === this._pendingMode));
    });
  }

  async _onEnterClicked() {
    // 授权摄像头/麦克风（getUserMedia 内建 AEC；PRD §3）。失败则提示但不崩。
    this.setPermStatus("正在请求摄像头/麦克风授权…");
    try {
      // 真实授权：拿到 stream 即接到 <video>（voice/posture 复用同一 stream/<video>）。
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: {
          echoCancellation: true, // 内建 AEC（PRD §3 自激规避前置）
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const video = document.getElementById("camera");
      if (video) {
        video.srcObject = stream;
      }
      this.setPermStatus("授权成功");
      // 把待选项一次性交给 main：main 连 WS + 等 config.push + 发 session.start。
      this._mode = this._pendingMode;
      this._voiceMode = this._pendingVoiceMode;
      this._subtitles = this._pendingSubtitles;
      this.showApp();
      this.applyModeUI(this._mode);
      this.applyVoiceModeUI(this._voiceMode);
      this.applySubtitlesUI(this._subtitles);
      if (this.h.onEnter) {
        this.h.onEnter({
          mode: this._mode,
          voiceMode: this._voiceMode,
          subtitles: this._subtitles,
          stream, // main 可把同一 stream 传给 voice（采音）/ posture（取 <video> 即可）
        });
      }
    } catch (err) {
      // 授权失败：保留在引导页，提示文字输入兜底仍可用（语音降级，PRD §5）。
      this.setPermStatus(`授权失败：${err && err.name ? err.name : err}（可进入后用文字输入）`);
      console.warn("[ui] getUserMedia 失败", err);
    }
  }

  setPermStatus(text) {
    if (this._el.permStatus) this._el.permStatus.textContent = text;
  }

  // ── landing ↔ app 切换 ──
  showApp() {
    if (this._el.landing) this._el.landing.hidden = true;
    if (this._el.app) this._el.app.hidden = false;
  }

  // ── 主界面控件绑定 ──────────────────────────────────────────────────────
  _bindAppControls() {
    const { modeGroup, voiceToggle, subtitlesToggle, textInput, textSend } = this._el;

    if (modeGroup) {
      modeGroup.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-mode]");
        if (!btn || btn.dataset.mode === this._mode) return;
        this._mode = btn.dataset.mode;
        this.applyModeUI(this._mode);
        if (this.h.onModeChange) this.h.onModeChange(this._mode); // → session.update{mode}
      });
    }

    if (voiceToggle) {
      voiceToggle.addEventListener("click", () => {
        // ptt ↔ free 翻转（PRD §4.3/§4.4）。
        this._voiceMode = this._voiceMode === VoiceMode.PTT ? VoiceMode.FREE : VoiceMode.PTT;
        this.applyVoiceModeUI(this._voiceMode);
        if (this.h.onVoiceModeChange) this.h.onVoiceModeChange(this._voiceMode);
      });
    }

    if (subtitlesToggle) {
      subtitlesToggle.addEventListener("change", () => {
        this._subtitles = !!subtitlesToggle.checked;
        this.applySubtitlesUI(this._subtitles);
        if (this.h.onSubtitlesToggle) this.h.onSubtitlesToggle(this._subtitles);
      });
    }

    const sendText = () => {
      if (!textInput) return;
      const text = textInput.value.trim();
      if (!text) return;
      textInput.value = "";
      if (this.h.onTextInput) this.h.onTextInput(text); // → text.input（语音降级兜底）
    };
    if (textSend) textSend.addEventListener("click", sendText);
    if (textInput) {
      textInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") sendText();
      });
    }
  }

  // ── 运行时 UI 应用（main 切 session 后也会回调到这里保持一致）──
  applyModeUI(mode) {
    this._mode = mode;
    const group = this._el.modeGroup;
    if (group) {
      group.querySelectorAll("[data-mode]").forEach((b) => {
        const on = b.dataset.mode === mode;
        b.classList.toggle("selected", on);
        b.setAttribute("aria-pressed", String(on));
      });
    }
    // 坐姿状态指示器仅 learning 显示（PRD §3.2.2 / 用户要求：其余模式不显示）。
    if (this._el.postureIndicator) {
      this._el.postureIndicator.hidden = mode !== Mode.LEARNING;
      if (mode === Mode.LEARNING) this.setPostureState("idle");
    }
  }

  applyVoiceModeUI(voiceMode) {
    this._voiceMode = voiceMode;
    const btn = this._el.voiceToggle;
    if (btn) {
      btn.dataset.voiceMode = voiceMode;
      btn.textContent = voiceMode === VoiceMode.PTT ? "对讲机（按住说话）" : "自由对话（VAD）";
    }
    // PTT 按钮仅在 ptt 模式下需要（free 下隐藏；自由对话免按）。
    const ptt = document.getElementById("ptt-btn");
    if (ptt) ptt.hidden = voiceMode !== VoiceMode.PTT;
  }

  applySubtitlesUI(on) {
    this._subtitles = on;
    if (this._el.subtitlesToggle) this._el.subtitlesToggle.checked = on;
    // 字幕开关只切换字幕列表 + 工具活动轻提示的显隐，**不连带隐藏整块面板**：
    // 文字输入兜底（.text-input-row）是语音/字幕都不便时的退路（PRD §5），
    // 恰是关字幕态下最需要的退路——绝不能随字幕一起消失。面板始终常显，
    // 字幕条目与工具活动按开关隐藏即可。
    if (this._el.transcriptList) this._el.transcriptList.hidden = !on;
    if (this._el.toolActivity && !on) this._el.toolActivity.hidden = true;
  }

  // ── 坐姿状态指示器（PRD §3.2；仅 learning 显示）──
  // state ∈ "idle"（无监测）| "ok"（坐姿正常）| "alert"（已触发提醒）
  setPostureState(state) {
    const el = this._el.postureIndicator;
    if (!el || el.hidden) return;
    el.dataset.state = state;
    const label = el.querySelector(".posture-label");
    const text = state === "alert" ? "坐姿提醒已发出" : state === "ok" ? "坐姿正常" : "坐姿监测中";
    if (label) label.textContent = text;
    else el.textContent = text;
  }

  // ── 字幕/历史面板（PRD §4.1；仅 subtitles=true 时有内容）──
  // 流式增量：final=false 时覆盖同角色最后一条「未定稿」条目；final=true 定稿。
  appendTranscript({ role, text, final }) {
    const list = this._el.transcriptList;
    if (!list) return;
    // 有内容要落地就让列表可见：关字幕时用户用文字兜底（main._onTextInput 本地回显），
    // 这条 user 回显是用户自己产出的反馈，必须看得见——否则发了字看不到自己发的什么（PRD §5）。
    // 字幕开关只决定「是否向后端请求转写」，不该吞掉用户自己的本地回显。
    if (list.hidden) list.hidden = false;
    const last = list.lastElementChild;
    const sameRoleDraft =
      last && last.dataset.role === role && last.dataset.final === "false";
    let item;
    if (sameRoleDraft) {
      item = last; // 覆盖上一条未定稿增量
    } else {
      item = document.createElement("div");
      item.className = "transcript-item";
      item.dataset.role = role;
      const who = document.createElement("span");
      who.className = "transcript-role";
      who.textContent = role === "user" ? "你" : "助手";
      const body = document.createElement("span");
      body.className = "transcript-text";
      item.append(who, body);
      list.appendChild(item);
    }
    item.dataset.final = String(!!final);
    const body = item.querySelector(".transcript-text");
    if (body) body.textContent = text;
    list.scrollTop = list.scrollHeight;
  }

  // ── 工具活动轻提示（tool.activity）──
  showToolActivity({ name, phase, summary }) {
    const el = this._el.toolActivity;
    if (!el) return;
    if (phase === "start") {
      el.textContent = summary || `正在使用：${name}…`;
      el.hidden = false;
    } else {
      // done：短暂保留 summary 再淡出（桩：直接清空）。
      el.textContent = summary || "";
      // TODO[M3+]：done 后做淡出动画/计时清除，当前即时清。
      if (!summary) el.hidden = true;
    }
  }

  // ── 状态 / 错误条 ──
  setStatus(text) {
    if (this._el.status) this._el.status.textContent = text;
  }

  showError({ code, message }) {
    const el = this._el.errorBanner;
    if (!el) return;
    el.textContent = message ? `${message}（${code}）` : code;
    el.hidden = false;
  }
  clearError() {
    if (this._el.errorBanner) this._el.errorBanner.hidden = true;
  }
}
