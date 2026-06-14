// Visual Assistant v0.1 · 前端协议助手（镜像 contracts/protocol.py + contracts/envelope.py）。
//
// 职责：把后端的「真理来源」契约（MessageType / Channel / Envelope 形态）镜像到前端，
//       提供 buildEnvelope() / parse() 两个纯函数，让其它模块只认常量、不手拼魔法字符串。
// 边界：纯数据/纯函数，无 WS、无 DOM、无副作用。跨进程消息的唯一收发外壳。
// 时序契约（contracts/envelope.py · protocol.py）：
//   单 WS 两类帧 —— ① 二进制帧 = 音频（PCM16 上行 / PCM24 下行），裸字节、不裹信封；
//                   ② 文本帧 = 控制 Envelope(JSON)。本文件只管文本帧（信封）。
//   Envelope = {type, ts(epoch ms), channel, payload, schema_version:"0.1"}（无 turn_id）。
//
// ⚠ 这是「接口字典」：新增/改名任何 type 或 channel，必须回头同步 contracts/protocol.py，
//   否则前后端契约分叉（铁律：跨模块只走总线消息）。

// 信封版本（contracts/envelope.py SCHEMA_VERSION）。
export const SCHEMA_VERSION = "0.1";

// 控制帧粗分组标签（contracts/protocol.py Channel）。决定收/发各自挂哪个 handler。
export const Channel = Object.freeze({
  SESSION: "session", // 会话生命周期（start/update/ready）
  AUDIO: "audio", // 音频轮次控制（PTT 边界 / 打断）；音频本体走二进制帧
  TRANSCRIPT: "transcript", // 字幕 + 工具活动提示（UI 展示）
  FRAME: "frame", // 摄像头单帧请求/回传（视觉工具触发时）
  POSTURE: "posture", // 坐姿守护（端侧 D 推给后端）
  CONTROL: "control", // 配置下发 / 文字输入兜底 / 错误
});

// 全量 WS 消息类型（contracts/protocol.py MessageType）。
export const MessageType = Object.freeze({
  // —— 会话生命周期（SESSION）——
  SESSION_START: "session.start", // 客户端→后端：开 Live 会话  payload {mode, voice_mode, subtitles, lat?, lon?}
  SESSION_UPDATE: "session.update", // 客户端→后端：运行时切换    payload {mode?, voice_mode?, subtitles?}
  SESSION_READY: "session.ready", // 后端→客户端：会话就绪      {session_id, mode, voice_mode}

  // —— 音频轮次控制（AUDIO）；音频本体是二进制帧 ——
  INPUT_ACTIVITY_START: "input.activity_start", // 客户端→后端：PTT 按下  payload {}
  INPUT_ACTIVITY_END: "input.activity_end", // 客户端→后端：PTT 松手  payload {}
  INTERRUPTED: "interrupted", // 后端→客户端：barge-in → 停播 + 清队列  {reason?}

  // —— 字幕与工具活动（TRANSCRIPT）——
  TRANSCRIPT: "transcript", // 后端→客户端：用户/助手转写  {role, text, final}
  TOOL_ACTIVITY: "tool.activity", // 后端→客户端：工具在动  {name, phase, summary?}

  // —— 摄像头单帧往返（FRAME）——
  FRAME_REQUEST: "frame.request", // 后端→客户端：要一帧  {request_id, kind}
  FRAME_RESPONSE: "frame.response", // 客户端→后端：回一帧  {request_id, jpeg_base64}

  // —— 坐姿守护（POSTURE）——
  POSTURE_ALERT: "posture.alert", // 客户端(端侧 D)→后端：驼背事件  {severity, ts, reminder_count?}

  // —— 控制面（CONTROL）——
  CONFIG_PUSH: "config.push", // 后端→客户端：前端阈值快照  {posture, voice}
  TEXT_INPUT: "text.input", // 客户端→后端：文字输入兜底  {text}
  ERROR: "error", // 后端→客户端：错误/降级  {code, message, degradation?}
});

// mode / voice_mode 枚举值（contracts/session.py Mode / VoiceMode）。
export const Mode = Object.freeze({
  OPEN: "open", // 基座（开放对话）
  LEARNING: "learning", // 收窄 profile：作业辅导 + 坐姿守护
  LIFE: "life", // 收窄 profile：天气穿搭 + 日常
});

export const VoiceMode = Object.freeze({
  PTT: "ptt", // 对讲机：按住说话（默认，确定性、防自激）
  FREE: "free", // 自由对话：模型原生 VAD + barge-in（高光）
});

// 视觉工具 kind（contracts/vision.py VisionKind）；frame.request.kind 取值之一。
export const VisionKind = Object.freeze({
  LOOK_AT_PAGE: "look_at_page",
  CHECK_DRAFT: "check_draft",
  OBSERVE: "observe",
});

/**
 * 构造一个出站控制信封（contracts/envelope.py Envelope）。
 * @param {string} type    MessageType 之一
 * @param {string} channel Channel 之一
 * @param {object} payload 按 type 对应的契约 payload（默认空对象，如 input.activity_*）
 * @returns {object} 可直接 JSON.stringify 后 ws.send 的信封对象
 */
export function buildEnvelope(type, channel, payload = {}) {
  return {
    type,
    ts: Date.now(), // epoch 毫秒（contracts 约定）
    channel,
    payload,
    schema_version: SCHEMA_VERSION,
  };
}

/**
 * 解析一帧入站文本（JSON 信封）。非法 JSON 返回 null（调用方告警跳过，不拖垮连接）。
 * 注意：音频是二进制帧，不会进这里（main.js 按 typeof 分流）。
 * @param {string} raw ws.message 的文本数据
 * @returns {object|null} 解析出的信封或 null
 */
export function parse(raw) {
  try {
    const env = JSON.parse(raw);
    // 最小自检：信封必须有 type（payload 的细 schema 由各 handler 自负，前端不全量校验）。
    if (!env || typeof env.type !== "string") return null;
    return env;
  } catch {
    return null;
  }
}
