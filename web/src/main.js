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

  ws.addEventListener("open", () => {
    setStatus("connected");
    // 装配端侧模块：同一条 ws 交给 B/D；二者自行按稳定 id querySelector DOM 占位。
    // cfg = 后端 config 下发的前端阈值，下发通道尚未接（无该契约消息）→ M1-01 先传 null 占位。
    try {
      initVoice(ws, null);
    } catch (e) {
      console.warn("initVoice 占位未实现（批1 接）：", e.message);
    }
    try {
      initPosture(ws, null);
    } catch (e) {
      console.warn("initPosture 占位未实现（批1 接）：", e.message);
    }
  });

  ws.addEventListener("message", (ev) => {
    // 入站信封：M1-01 仅记录以验证双向往返；按 channel 分发给 B/D 由后续里程碑接。
    try {
      const env = JSON.parse(ev.data);
      console.debug("ws ↓ 信封", env.type, env.channel, env.turn_id);
    } catch (e) {
      console.warn("收到非 JSON 帧：", e.message);
    }
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
  };

  return ws;
}

// 模块加载即装配（DOM 就绪后连 /ws）。
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main);
} else {
  main();
}
