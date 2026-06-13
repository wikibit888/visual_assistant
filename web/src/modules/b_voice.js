// 模块 B（前端半）· 语音 I/O（PRD §7.1 / §7.5）。M0 骨架。
// 职责：VAD（vad-web）/ 对讲机 PTT / 播放队列 / 半双工 gate。
// - 开场默认对讲机（config.turn_state.default_voice_mode=ptt）；自由对话(VAD)作高光。
// - AI_SPEAKING 期间半双工 gate 麦克风（暂停采音，消灭自激）。
// - 上行 asr.final（经后端 ASR）；下行 tts.say/stop；stop=立即停+清队列+回 tts.ack。
// 数值（vad_speaking_min_ms 等）由后端 config 下发，前端不硬编码阈值。

export function initVoice(/* ws, cfg */) {
  throw new Error("M1 语音链路：VAD/PTT/播放队列/半双工 gate");
}
