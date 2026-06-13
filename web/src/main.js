// Visual Assistant v0.1 · 前端入口（PRD §7.1）。M1-01：装配，禁业务逻辑。
// 职责：建立单 WebSocket（契约一信封，JSON 文本帧）；装配 B 语音、D 姿态两端侧模块。
// 跨模块只走信封；D 只发 posture.alert，不出声、不入 agent loop（铁律）。
// b_voice / d_posture 由批1 实现；本文件只负责 import + 连 /ws + 把 ws 交给二者装配。

import { initVoice } from "./modules/b_voice.js";
import { initPosture } from "./modules/d_posture.js";

// WS 地址：同源 ws(s)://host/ws（https→wss）。前端不带阈值魔数（阈值由后端 config 下发）。
function wsURL() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

export function main() {
  const statusEl = document.getElementById("status");
  const setStatus = (s) => {
    if (statusEl) statusEl.textContent = s;
  };

  const ws = new WebSocket(wsURL());
  let cfg = null; // 后端 config.push 下发的前端阈值快照（建连后到达）；前端不自带魔数。
  let assembled = false; // B/D 仅装配一次（首个 config.push 触发）。

  // 收到 config.push 后再装配 B/D，确保 cfg 在 init 时即为非 null 对象（阈值由后端下发）。
  // 保留 try/catch 容错：批1 的 b_voice/d_posture 尚是骨架，会抛 NotImplementedError。
  function assembleModules() {
    if (assembled) return;
    assembled = true;
    try {
      initVoice(ws, cfg);
    } catch (e) {
      console.warn("initVoice 占位未实现（批1 接）：", e.message);
    }
    try {
      initPosture(ws, cfg);
    } catch (e) {
      console.warn("initPosture 占位未实现（批1 接）：", e.message);
    }
  }

  ws.addEventListener("open", () => {
    setStatus("connected");
    // 不在 open 即装配：等后端 config.push 到达拿到 cfg 再 init（保证 cfg 非 null）。
  });

  ws.addEventListener("message", (ev) => {
    let env;
    try {
      env = JSON.parse(ev.data);
    } catch (e) {
      console.warn("收到非 JSON 帧：", e.message);
      return;
    }
    if (env.type === "config.push") {
      // A 的控制面下发：缓存前端阈值快照（turn_state/posture），并据此装配 B/D（仅一次）。
      cfg = env.payload;
      console.debug("ws ↓ config.push 已缓存 cfg", cfg);
      assembleModules();
      return;
    }
    // 其它入站信封：M1-01 仅记录以验证双向往返；按 channel 分发给 B/D 由后续里程碑接。
    console.debug("ws ↓ 信封", env.type, env.channel, env.turn_id);
  });

  ws.addEventListener("close", () => setStatus("disconnected"));
  ws.addEventListener("error", (e) => {
    setStatus("error");
    console.error("ws 错误", e);
  });

  // 手动回环验证钩子（MOCK 全开时，在浏览器 console 调 window.__va.sendEnvelope(env)）。
  window.__va = {
    ws,
    sendEnvelope(env) {
      ws.send(JSON.stringify(env));
    },
    get cfg() {
      return cfg;
    }, // config.push 下发后非 null（冒烟：window.__va.cfg.posture.hunchback_hold_ms）
  };

  return ws;
}

// 模块加载即装配（DOM 就绪后连 /ws）。
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main);
} else {
  main();
}
