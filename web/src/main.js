// Visual Assistant v0.1 · 前端入口（PRD §7.1）。M0 骨架，禁业务逻辑。
// 职责：建立单 WebSocket（契约一信封）；装配 B 语音与 D 姿态两个端侧模块。
// 跨模块只走信封；D 只发 posture.alert，不出声、不入 agent loop（铁律）。

// import { initVoice } from "./modules/b_voice.js";
// import { initPosture } from "./modules/d_posture.js";

export function main() {
  // M1：连 /ws、装配 b_voice、装配 d_posture
  throw new Error("M1：前端装配（WS + b_voice + d_posture）");
}
