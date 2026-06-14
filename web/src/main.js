// Visual Assistant v0.1 · 前端入口（Live 架构，PRD §3 / §4）。
//
// 职责：① 装配 UI（引导页/主界面）；② 连单 WS；③ 会话生命周期（session.start/update）；
//       ④ 下行事件路由（config.push / session.ready / transcript / tool.activity /
//          frame.request / interrupted / error + 二进制音频）；⑤ 把各模块（voice/posture/
//          client_state）按协议接到一起。
// 边界（铁律）：跨进程只走协议；前端只收/发 contracts/protocol.py 列的 type，不新铸消息。
//       前端阈值全从 config.push 读（client_state.initFromConfig），不自带魔数。
//
// 时序：DOMContentLoaded → UI.mount（引导页）→ 用户选模式/授权/进入 →（onEnter）连 WS →
//       收 config.push（装配 client_state/voice/posture）→ 收 session.ready 前已可发 session.start →
//       开放对话动线：连 → session.start → 字幕显示 → PTT 采音 → 收下行。

import { UI } from "./modules/ui.js";
import { Voice } from "./modules/voice.js";
import { Posture } from "./modules/posture.js";
import { ClientState } from "./modules/client_state.js";
import {
  buildEnvelope,
  parse,
  MessageType,
  Channel,
  VisionKind,
} from "./modules/protocol.js";

// 默认同源 ws(s)://host/ws（https→wss）。前后端分端口调试时用 ?api=host:port 覆盖后端地址
// （前端静态服务器在 8080、后端 WS 在 8000 → 打开 http://localhost:8080/?api=localhost:8000）。
// 前端不带阈值魔数（阈值由 config.push 下发）；?api 只覆盖 WS 目标 host，不引入业务魔数。
function wsURL() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const apiHost = new URLSearchParams(location.search).get("api") || location.host;
  return `${proto}//${apiHost}/ws`;
}

class App {
  constructor() {
    this.state = new ClientState(); // 蓝层确定性状态（active_problem / reminder_count / gap / mic gate）
    this.ws = null;
    this.cfg = null; // config.push 缓存（建连即到）
    this.assembled = false; // voice/posture 仅装配一次（首个 config.push 触发）
    this.session = null; // 用户在引导页选的 {mode, voiceMode, subtitles, stream}
    this.voice = null;
    this.posture = null;

    // UI 把「用户意图」回调上来，App 决定发什么 WS。
    this.ui = new UI({
      onEnter: (sel) => this._onEnter(sel), // 引导页进入 → 连 WS + 发 session.start
      onModeChange: (mode) => this._onModeChange(mode),
      onVoiceModeChange: (vm) => this._onVoiceModeChange(vm),
      onSubtitlesToggle: (on) => this._onSubtitlesToggle(on),
      onTextInput: (text) => this._onTextInput(text),
    });
  }

  start() {
    this.ui.mount();
    this.ui.setStatus("请选择模式并进入");
  }

  // ── 引导页进入：拿到选择 → 连 WS（config.push 到达后装配 + 发 session.start）──
  _onEnter(sel) {
    this.session = sel;
    this.state.setMode(sel.mode); // 立即写确定性 mode（坐姿放行依赖它）
    this._connect();
  }

  _connect() {
    this.ui.setStatus("连接中…");
    this.ws = new WebSocket(wsURL());
    this.ws.binaryType = "arraybuffer"; // 下行音频 = 二进制 ArrayBuffer（PCM24）

    this.ws.addEventListener("open", () => {
      this.ui.setStatus("已连接（等待配置…）");
      // 不在 open 即发 session.start：等 config.push 到达、装配好模块再发（保证 cfg 非 null）。
    });

    this.ws.addEventListener("message", (ev) => this._onMessage(ev));

    this.ws.addEventListener("close", () => {
      this.ui.setStatus("已断开");
      // PRD §5：语音链路无离线退路；断网时文字输入兜底仍可用（text.input 需 WS，故提示重连）。
      this.ui.showError({ code: "live_disconnected", message: "连接断开，请刷新重连" });
    });
    this.ws.addEventListener("error", (e) => {
      this.ui.setStatus("连接错误");
      console.error("[main] ws error", e);
    });
  }

  // ── 下行帧分流：二进制=音频（PCM24），文本=控制信封 ──
  _onMessage(ev) {
    // 二进制帧 = 下行音频（PCM24，裸字节、不裹信封；contracts/envelope.py）。
    if (ev.data instanceof ArrayBuffer) {
      if (this.voice) this.voice.enqueueDownlink(ev.data);
      // 收到下行音频 = 链路已恢复正常 → 撤掉残留错误条（自愈，不靠刷新）。错误条只在 session.ready
      // 清不够：后端错误（live_disconnected/server_error）不关 WS，恢复后无新 session.ready，残留不消。
      this.ui.clearError();
      return;
    }
    const env = parse(ev.data);
    if (!env) {
      console.warn("[main] 丢弃非法信封");
      return;
    }
    this._routeEnvelope(env);
  }

  // ── 下行控制事件路由（contracts/protocol.py 后端→客户端集合）──
  _routeEnvelope(env) {
    switch (env.type) {
      case MessageType.CONFIG_PUSH: // 建连即到：前端阈值快照（前端不自带魔数）
        this.cfg = env.payload;
        this.state.initFromConfig(this.cfg);
        this._assembleModules();
        this._sendSessionStart(); // 拿到 cfg + 装配后才发 session.start（开放对话动线起点）
        break;

      case MessageType.SESSION_READY: { // 会话就绪，**回声当前实际生效配置**（契约 SessionReady）
        const { session_id, mode, voice_mode } = env.payload || {};
        // 后端可能因 profile/能力限制把请求的 mode/voice_mode 落到别的值——
        // 一律以后端回声为准对齐 UI 与确定性状态，避免「UI 撒谎」（与服务端漂移）。
        // 仅在与本地不一致时才真正回灌（一致则静默，省去无谓重渲染）。
        if (mode && mode !== this.state.mode) {
          this.state.setMode(mode); // 确定性 mode（坐姿放行依据 + 坐姿指示器显隐）
          this.ui.applyModeUI(mode); // 右上模式高亮 + 坐姿指示器显隐对齐后端真值
          console.warn("[main] session.ready mode 漂移，已对齐后端", mode);
        }
        if (voice_mode && this.voice && voice_mode !== this.voice.voiceMode) {
          this.voice.setVoiceMode(voice_mode); // 采麦策略对齐（ptt↔free）
          this.ui.applyVoiceModeUI(voice_mode); // 语音切换按钮文案/PTT 显隐对齐后端真值
          console.warn("[main] session.ready voice_mode 漂移，已对齐后端", voice_mode);
        }
        this.ui.setStatus(`会话就绪（${mode}/${voice_mode}）`);
        this.ui.clearError();
        console.debug("[main] session.ready", session_id);
        break;
      }

      case MessageType.TRANSCRIPT: // 字幕/历史（仅 subtitles=true 时来）
        this.ui.clearError(); // 收到后端转写 = 链路在工作 → 撤掉残留错误条（仅后端转写会进此分支，本地回显不走 WS）
        this.ui.appendTranscript(env.payload || {});
        break;

      case MessageType.TOOL_ACTIVITY: { // 工具在动；learning 下 look_at_page done → 置 active_problem
        const a = env.payload || {};
        this.ui.showToolActivity(a);
        if (
          this.state.mode === "learning" &&
          a.name === VisionKind.LOOK_AT_PAGE &&
          a.phase === "done"
        ) {
          this.state.setActiveProblem({ at: Date.now() }); // PRD §3.2.2 坐姿放行依据
          console.debug("[main] active_problem 置位（look_at_page done）");
        }
        break;
      }

      case MessageType.FRAME_REQUEST: // 后端要一帧 → 抓当前视频帧 JPEG → frame.response
        this._respondFrame(env.payload || {});
        break;

      case MessageType.INTERRUPTED: // barge-in：立即停播 + 清播放队列
        if (this.voice) this.voice.onInterrupted((env.payload || {}).reason);
        break;

      case MessageType.ERROR: { // 错误/降级提示
        const e = env.payload || {};
        this.ui.showError(e);
        // TODO[M2+]：据 e.degradation 自动降级（切对讲机 / 显式文字输入兜底）。当前仅展示。
        console.warn("[main] error", e);
        break;
      }

      default:
        console.debug("[main] 未路由信封", env.type, env.channel);
    }
  }

  // ── 装配 voice/posture（首个 config.push 后一次性）──
  _assembleModules() {
    if (this.assembled) return;
    this.assembled = true;
    const stream = this.session && this.session.stream;

    try {
      this.voice = new Voice(this.ws, this.state);
      // 把 config.push 缓存（cfg）传入：voice 从 cfg.audio 读上/下行采样率（禁前端硬编码采样率）。
      this.voice.init({ stream, voiceMode: this.session.voiceMode, cfg: this.cfg });
    } catch (e) {
      console.warn("[main] voice 装配失败（桩可容错）", e && e.message);
    }
    try {
      this.posture = new Posture(this.ws, this.state);
      this.posture.attachUI(this.ui);
      this.posture.init();
    } catch (e) {
      console.warn("[main] posture 装配失败（桩可容错）", e && e.message);
    }
  }

  // ── 会话生命周期：start / update ──
  _sendSessionStart() {
    const { mode, voiceMode, subtitles } = this.session;
    this._send(MessageType.SESSION_START, Channel.SESSION, {
      mode,
      voice_mode: voiceMode,
      subtitles,
    });
  }

  _onModeChange(mode) {
    this.state.setMode(mode); // 写确定性 mode（离开 learning 清 active_problem，防误吞）
    this._send(MessageType.SESSION_UPDATE, Channel.SESSION, { mode });
  }

  _onVoiceModeChange(voiceMode) {
    if (this.voice) this.voice.setVoiceMode(voiceMode); // 改采麦策略（ptt↔free）
    this._send(MessageType.SESSION_UPDATE, Channel.SESSION, { voice_mode: voiceMode });
  }

  _onSubtitlesToggle(on) {
    this._send(MessageType.SESSION_UPDATE, Channel.SESSION, { subtitles: on });
  }

  _onTextInput(text) {
    // 文字输入兜底（语音降级；PRD §5）。
    this._send(MessageType.TEXT_INPUT, Channel.CONTROL, { text });
    // 本地也回显为 user 字幕（即时反馈；后端真转写若来会覆盖/补充）。
    this.ui.appendTranscript({ role: "user", text, final: true });
  }

  // ── frame.request → 抓 <video> 当前帧 JPEG base64 → frame.response（contracts/frame.py）──
  _respondFrame({ request_id, kind }) {
    if (!request_id) return;
    const video = document.getElementById("camera");
    const canvas = document.getElementById("frame-canvas");
    let jpeg_base64 = "";
    try {
      if (video && canvas && video.videoWidth > 0) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        // toDataURL 去掉 "data:image/jpeg;base64," 前缀（契约要求纯 base64）。
        const dataUrl = canvas.toDataURL("image/jpeg", 0.8);
        jpeg_base64 = dataUrl.split(",")[1] || "";
      } else {
        console.warn("[main] frame.request 时无可用视频帧（桩回空）", kind);
      }
    } catch (e) {
      console.warn("[main] 抓帧失败（回空，不崩）", e && e.message);
    }
    this._send(MessageType.FRAME_RESPONSE, Channel.FRAME, { request_id, jpeg_base64 });
  }

  _send(type, channel, payload) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn("[main] WS 未就绪，丢弃出站", type);
      return;
    }
    this.ws.send(JSON.stringify(buildEnvelope(type, channel, payload)));
  }
}

// 模块加载即装配（DOM 就绪后渲染引导页）。
function boot() {
  const app = new App();
  app.start();
  // 联调钩子（console 可调；MOCK 全开时手动验回环 / 导演触发坐姿）。
  window.__va = {
    app,
    get cfg() {
      return app.cfg;
    },
    get state() {
      return app.state;
    },
    forcePosture() {
      if (app.posture) app.posture.forceCandidate();
    },
  };
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
