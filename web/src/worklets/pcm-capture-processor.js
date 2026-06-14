// Visual Assistant v0.1 · 上行采集 AudioWorklet（PRD §3 / §4.3 / §5）。
//
// 职责：在音频渲染线程把麦克风 Float32 块转 PCM16（裸 16-bit 有符号小端、单声道），
//       累积到约 N ms 一块后 postMessage(Int16Array.buffer) 给主线程上送（主线程 ws.send 二进制）。
// 边界（房规）：worklet 跑在独立 AudioWorkletGlobalScope —— 无 import、无 DOM、无 WS；
//       只 class extends AudioWorkletProcessor + registerProcessor。一切配置经构造期
//       processorOptions / 运行期 port.postMessage 注入，本文件不带业务魔数。
//
// 半双工 mic gate（PRD §5 消自激）：AI 说话期（下行在播 / 坐姿播报）主线程 postMessage('gate',true)，
//   本 worklet 进入静默——直接丢弃输入块、不向主线程上送（采麦在播报期物理断开自激链）。
//   gate 也由主线程据 PTT/free 策略开关；本 worklet 不自己判轮次（轮次判定塌进 Live 模型，PRD §4.4）。
//
// 采样率：浏览器已用 AudioContext({sampleRate: in_sample_rate}) 把麦克风重采样到 16k，
//   故本 worklet 不做任何重采样——只做 Float32→Int16 量化 + 分块（采样率单一真源 = config.audio）。

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    // 每块目标采样数：由主线程按 (in_sample_rate * frameMs / 1000) 算好下发（不在 worklet 里拿魔数）。
    // 缺失则回落到「每个渲染量子（128）即发」——退化但仍能工作，不臆造时长魔数。
    this._blockSamples =
      typeof opts.blockSamples === "number" && opts.blockSamples > 0
        ? opts.blockSamples : 128;
    // gate 初值：自由对话建连可能恰逢 AI 说话；默认开麦（false=不 gate），由主线程随状态校正。
    this._gated = !!opts.gated;

    // 累积缓冲：满 _blockSamples 即打包成一个 Int16Array.buffer 发出（分块上送，省帧数）。
    this._buf = new Int16Array(this._blockSamples);
    this._filled = 0;

    // 主线程控制通道：仅 'gate'（开关静默）。其余消息忽略（worklet 不做业务路由）。
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m && m.type === "gate") this._gated = !!m.on;
    };
  }

  // 渲染线程逐量子回调：inputs[0][0] = 第一输入第一声道的 Float32（长度通常 128）。
  process(inputs) {
    const input = inputs[0];
    // 无输入（未接源/轨道结束）：保持节点存活（返回 true），等下次有数据。
    if (!input || input.length === 0) return true;
    const ch = input[0];
    if (!ch) return true;

    // 半双工 gate 期：丢弃本块、不累积、不上送（消自激，PRD §5）。仍返回 true 保持节点存活。
    if (this._gated) return true;

    for (let i = 0; i < ch.length; i++) {
      // Float32[-1,1] → Int16：先 clamp 再 *32767（正负对称量化，避免溢出回绕爆音）。
      let s = ch[i];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      this._buf[this._filled++] = (s * 32767) | 0;

      if (this._filled === this._blockSamples) {
        // 满一块：拷出 buffer 转移给主线程（transfer 转移所有权，零拷贝），再重置累积区。
        const out = this._buf.slice(0, this._blockSamples);
        this.port.postMessage(out.buffer, [out.buffer]);
        this._filled = 0;
      }
    }
    return true; // 常驻：节点存活直到主线程 disconnect（采麦停止由主线程控制）。
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
