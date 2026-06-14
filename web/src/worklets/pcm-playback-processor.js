// Visual Assistant v0.1 · 下行播放 AudioWorklet（PRD §4.1 / §4.4 / §5）。
//
// 职责：维护一个 Float32 环形缓冲；主线程把下行 PCM24（已转 Float32）postMessage 进来填缓冲，
//       process() 每渲染量子从环形缓冲拉样本填 output，欠载补零（边收边播、首块先播）。
// 边界（房规）：独立 AudioWorkletGlobalScope —— 无 import、无 DOM、无 WS；只 class + registerProcessor。
//
// barge-in（PRD §4.4 / §5）：主线程收 interrupted → postMessage('clear') → 本 worklet 立即清空
//   环形缓冲（丢弃挂起音频，停声）。清空 = 读写指针归零 + 已缓冲样本数置零。
// drained 通知（去抖，PRD §5）：缓冲拉空后须**连续空 ≥ M 个渲染量子**才判「播报真结束」，
//   再 postMessage({type:'drained'}) 给主线程 → 冷却后解半双工 mic gate（消自激）。本 worklet 不碰 gate 本身。
//   为何去抖：块入队是主线程异步 postMessage，流式块间有微小间隙时某个量子可能恰好拉空（_count 瞬时为 0），
//   单量子瞬时空 ≠ 播报结束；句中误判 drained 会让主线程过早解 gate，自由对话下自激风险上升。
//   M 由 drainQuietMs（去抖静默窗，主线程从 config.voice 下发）÷ 单量子时长换算，不在此拿魔数。
//
// 采样率：AudioContext 用 out_sample_rate(24k) 建，故 output 即 24k，本 worklet 不做重采样。
// 缓冲容量由主线程经 processorOptions.ringCapacity 下发（按 out_sample_rate * 缓冲秒数算，不在此拿魔数）。

class PcmPlaybackProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    // 环形缓冲容量（样本数）：主线程按 out_sample_rate × 秒数算好下发；缺失回落一个保守容量，
    // 仅为不崩（不臆造「秒数」业务魔数，纯防御性兜底，足够装若干渲染量子）。
    const cap =
      typeof opts.ringCapacity === "number" && opts.ringCapacity > 0
        ? Math.floor(opts.ringCapacity) : 24000;
    this._ring = new Float32Array(cap);
    this._cap = cap;
    this._read = 0; // 读指针
    this._write = 0; // 写指针
    this._count = 0; // 当前已缓冲样本数（0..cap）
    this._wasPlaying = false; // 上一量子是否在出声（用于检测 drained 下沿）

    // drained 去抖：连续空量子计数 + 阈值 M。drainQuietMs 来自主线程（config.voice.playback_drain_quiet_ms）；
    // M 在首个量子拿到真实量子长度后据 sampleRate 换算（量子长度运行时才确定，故惰性算）。
    this._drainQuietMs =
      typeof opts.drainQuietMs === "number" && opts.drainQuietMs > 0 ? opts.drainQuietMs : 0;
    this._emptyQuanta = 0; // 连续「本量子拉空」计数
    this._drainNotified = false; // 已就本段静默发过 drained（防重复发；有音频回流即复位）
    this._quietQuantaNeeded = 0; // M：判 drained 所需的连续空量子数（首量子惰性算）

    // 起播预缓冲（去抖）：缓冲首次填充期先静音攒样本，攒够 prebufferSamples（或等待超上限）才开播，
    // 避免开头流式块间抖动欠载补零的咔哒声。上限保证短回答攒不够也能及时开播（不卡死）。
    this._prebufferSamples =
      typeof opts.prebufferSamples === "number" && opts.prebufferSamples > 0
        ? Math.floor(opts.prebufferSamples) : 0;
    this._priming = this._prebufferSamples > 0; // 是否处于起播预缓冲期（静音攒样本）
    this._primeQuanta = 0; // priming 已过量子数（与上限比较）
    this._maxPrimeQuanta = 0; // priming 等待上限（首量子惰性按真实量子长度算）

    this.port.onmessage = (e) => {
      const m = e.data;
      if (!m) return;
      if (m.type === "clear") {
        // barge-in：立即清空环形缓冲（停声 + 丢弃挂起音频）。
        this._read = 0;
        this._write = 0;
        this._count = 0;
        // 不在此发 drained：clear 是主线程主动触发，主线程已自行处理 gate/队列。
        this._wasPlaying = false;
        this._emptyQuanta = 0;
        this._drainNotified = false;
        this._priming = this._prebufferSamples > 0; // 重新起播也走预缓冲
        this._primeQuanta = 0;
      } else if (m.samples) {
        // 入队一块 Float32（主线程已把 PCM16 LE @24k 转成 Float32[-1,1]）。
        this._push(m.samples);
      }
    };
  }

  // 写入一块样本到环形缓冲；溢出（生产快于消费）则丢弃最旧样本（推进读指针），保实时不堆积延迟。
  _push(samples) {
    for (let i = 0; i < samples.length; i++) {
      this._ring[this._write] = samples[i];
      this._write = (this._write + 1) % this._cap;
      if (this._count < this._cap) {
        this._count++;
      } else {
        // 满：覆盖最旧，读指针跟进（丢最旧样本，宁丢旧音频不积压延迟——实时性优先，PRD §5）。
        this._read = (this._read + 1) % this._cap;
      }
    }
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    if (!output || output.length === 0) return true;
    const out = output[0]; // 单声道
    const n = out.length;

    // —— 起播预缓冲（去抖）：攒够 prebufferSamples（或等待超上限）前先静音、不推进读指针、不判 drained ——
    if (this._priming) {
      if (this._maxPrimeQuanta === 0) {
        // 上限惰性算：max(预缓冲对应量子的 4 倍, ~200ms 对应量子)，保证短回答攒不够也能及时开播。
        const prebufQuanta = Math.max(1, Math.ceil(this._prebufferSamples / n));
        const capQuanta = Math.ceil((200 * sampleRate) / 1000 / n);
        this._maxPrimeQuanta = Math.max(prebufQuanta * 4, capQuanta);
      }
      // 仅在「本段已开始进样」（_count>0）后才计超时：否则空闲等待期（播放链早建好、用户还没说话）
      // 会把上限耗光，导致第一轮真音频到来时已不再预缓冲。空缓冲时只静默等待，不推进上限计数。
      if (this._count > 0) this._primeQuanta++;
      if (this._count >= this._prebufferSamples || this._primeQuanta >= this._maxPrimeQuanta) {
        this._priming = false; // 攒够 / 超上限 → 开播
      } else {
        for (let i = 0; i < n; i++) out[i] = 0; // 预缓冲期静音
        for (let c = 1; c < output.length; c++) output[c].set(out);
        return true; // 尚未开播：不拉样本、不判 drained
      }
    }

    let pulled = 0;
    for (let i = 0; i < n; i++) {
      if (this._count > 0) {
        out[i] = this._ring[this._read];
        this._read = (this._read + 1) % this._cap;
        this._count--;
        pulled++;
      } else {
        out[i] = 0; // 欠载补零（静音填充，不爆音）
      }
    }
    // 多声道节点：把声道 0 复制到其余声道（保持单声道源在立体声输出下也正常）。
    for (let c = 1; c < output.length; c++) {
      output[c].set(out);
    }

    const playingNow = pulled > 0;

    // —— drained 去抖判定（PRD §5）：不再用「单量子瞬时空缓冲」近似播报结束，
    //    改为「连续 ≥ M 个量子完全拉空」才判真 drained，吸收流式块间抖动，防句中误解 gate。
    if (playingNow || this._count > 0) {
      // 本量子出了声 / 缓冲里仍有音频 = 还在播 → 连续空计数清零、解锁下次 drained 通知。
      this._emptyQuanta = 0;
      this._drainNotified = false;
    } else {
      // 本量子完全没音频（空缓冲且没拉到样本）→ 累计连续空量子。
      this._emptyQuanta++;
      // M 惰性算（首量子才知道真实量子长度 n 与 sampleRate）：连续空时长 ≥ drainQuietMs 即判真 drained。
      // drainQuietMs<=0（主线程未下发）→ 回落 M=1（退回原单量子行为，不臆造去抖窗，契约·配置）。
      if (this._quietQuantaNeeded === 0) {
        if (this._drainQuietMs > 0) {
          const quantumMs = (n * 1000) / sampleRate; // sampleRate = AudioWorkletGlobalScope 全局
          this._quietQuantaNeeded = Math.max(1, Math.ceil(this._drainQuietMs / quantumMs));
        } else {
          this._quietQuantaNeeded = 1;
        }
      }
      // 仅在「曾出过声」且连续空达阈值、且本段尚未通知过时发一次 drained（下沿 + 去抖 + 防重复）。
      if (
        this._wasPlaying &&
        !this._drainNotified &&
        this._emptyQuanta >= this._quietQuantaNeeded
      ) {
        this._drainNotified = true;
        this.port.postMessage({ type: "drained" });
        // 本段播报结束 → 重新武装预缓冲，下一轮回答开头同样攒够再播（去抖，免轮间欠载咔哒）。
        this._priming = this._prebufferSamples > 0;
        this._primeQuanta = 0;
      }
    }
    // _wasPlaying 跟踪「本段是否曾出过声」：出声即置真；只有在重新出声后才允许下一次 drained 下沿。
    if (playingNow) this._wasPlaying = true;

    return true; // 常驻节点（断连由主线程 disconnect）。
  }
}

registerProcessor("pcm-playback", PcmPlaybackProcessor);
