// Visual Assistant v0.1 · 客户端确定性状态（蓝层，PRD §4.1 / §3.2.2 / §5）。
//
// 职责：持有「立场与时序」的确定性状态，供 voice & posture 读写。模型碰不到这些值
//       （铁律：非确定性只许出现在「语言与策略」，不许出现在「立场与时序」）。
// 边界：纯状态容器 + 纯判定函数；无 WS、无 DOM、无定时器。所有阈值由 init(cfg) 注入，
//       本文件禁硬编码任何魔数（缺键只告警，不臆造默认 —— 暴露上游 config 契约缺漏）。
//
// 确定性落点（PRD §5「确定性的三个落点」之②：客户端状态与闸门）：
//   - active_problem   ：learning 下工具 look_at_page done 即置位（坐姿放行依据，PRD §3.2.2）
//   - reminder_count   ：坐姿提醒累计次数（模型把「第 N 次」缝进措辞，但计数在客户端）
//   - mic gate         ：半双工闸门（自由对话 / 坐姿播报期 gate 麦克风，消自激，PRD §5）
//   - gap 判定         ：最近一次下行音频时刻 → 静默时长 ≥ gap_min_silence_ms 才算缝隙
//
// 时序契约：所有读写都同步、即时；不缓存跨帧推断。voice/posture 每次决策都现读当前值。

export class ClientState {
  constructor() {
    // —— 由 config.push 注入的阈值（init 后非 null；缺键时为 undefined，调用处告警）——
    this._cfg = null; // 整个 config.push payload（{posture, voice}）
    this._gapMinSilenceMs = undefined; // cfg.posture.gap_min_silence_ms
    this._reminderCooldownMs = undefined; // cfg.posture.reminder_cooldown_ms
    this._releaseScope = undefined; // cfg.posture.release_scope（如 "active_problem"）
    this._halfDuplexGate = undefined; // cfg.voice.half_duplex_gate

    // —— 运行时确定性状态 ——
    this._mode = null; // 当前 mode（"open"|"learning"|"life"）；UI 显式选，写入这里
    this._activeProblem = null; // 当前活跃题目标记（learning + look_at_page done → 非 null）
    this._reminderCount = 0; // 坐姿提醒累计次数
    this._lastReminderTs = 0; // 上次发出坐姿 alert 的时刻（冷却判定用）
    this._lastDownlinkAudioTs = 0; // 最近一次收到下行音频 chunk 的时刻（gap 判定用）
    this._micGated = false; // 麦克风是否被半双工闸门关闭（true=正在播报/坐姿，gate 采音）
  }

  /**
   * 用 config.push 下发的阈值初始化（main.js 在首个 config.push 后调用）。
   * 前端不自带魔数：阈值全从 cfg 读；缺键只 console.warn，不填默认值。
   * @param {object} cfg config.push payload，形如 {posture:{...}, voice:{...}}
   */
  initFromConfig(cfg) {
    this._cfg = cfg || {};
    const p = this._cfg.posture || {};
    const v = this._cfg.voice || {};

    this._gapMinSilenceMs = this._require(p, "gap_min_silence_ms", "posture");
    this._reminderCooldownMs = this._require(p, "reminder_cooldown_ms", "posture");
    this._releaseScope = this._require(p, "release_scope", "posture");
    this._halfDuplexGate = this._require(v, "half_duplex_gate", "voice");
  }

  // 读阈值缺键 → 告警并返回 undefined（暴露契约缺漏，不臆造默认）。
  _require(obj, key, subtree) {
    if (obj == null || obj[key] === undefined) {
      console.warn(
        `[client_state] config.push 缺键 ${subtree}.${key}（上游 config.yaml 契约缺漏，不臆造默认）`,
      );
      return undefined;
    }
    return obj[key];
  }

  // ── mode（UI 显式选，写入确定性状态；切到非 learning 时清 active_problem，防误吞 alert）──
  setMode(mode) {
    this._mode = mode;
    // PRD §5：mode 抖动吞 alert 的反向风险——离开 learning 即丢 active_problem（无主对话则不放行坐姿）。
    if (mode !== "learning") this._activeProblem = null;
  }
  get mode() {
    return this._mode;
  }

  // ── active_problem（PRD §3.2.2 坐姿放行依据；tool.activity look_at_page done 置位）──
  setActiveProblem(marker) {
    this._activeProblem = marker;
  }
  clearActiveProblem() {
    this._activeProblem = null;
  }
  get activeProblem() {
    return this._activeProblem;
  }

  /**
   * 坐姿放行判定（PRD §3.2.2）：mode==learning 或 active_problem!=null 才放行提醒。
   * release_scope 从 config 下发（如 "active_problem"）；此处实现「learning 或有活跃题」的口径。
   * @returns {boolean} 是否允许这次坐姿事件继续往 gap 闸门走
   */
  isPostureReleased() {
    return this._mode === "learning" || this._activeProblem != null;
  }

  // ── reminder_count（坐姿提醒累计；模型把「第 N 次」缝进措辞，计数在客户端）──
  get reminderCount() {
    return this._reminderCount;
  }
  // 一次坐姿 alert 真正发出时调用：累加次数 + 记录时刻（供冷却判定）。
  noteReminderSent(ts) {
    this._reminderCount += 1;
    this._lastReminderTs = ts;
  }
  // 同类提醒冷却（PRD §3.2 reminder_cooldown_ms）：距上次发出未满冷却则压住。
  isReminderInCooldown(now) {
    if (this._reminderCooldownMs === undefined) return false; // 缺阈值不臆造，保守不冷却（已告警）
    if (this._lastReminderTs === 0) return false;
    return now - this._lastReminderTs < this._reminderCooldownMs;
  }

  // ── gap 判定（PRD §4.2）：最近下行音频静默 ≥ gap_min_silence_ms 才算缝隙 ──
  noteDownlinkAudio(ts) {
    this._lastDownlinkAudioTs = ts;
  }
  /**
   * 当前是否处于「会话缝隙」（可注入坐姿 alert）。
   * 无阈值（缺键）→ 返回 false 并已告警（保守不放行，避免抢话）。
   * @param {number} now Date.now()
   */
  isInGap(now) {
    if (this._gapMinSilenceMs === undefined) return false;
    // 从未收到下行音频（_lastDownlinkAudioTs===0）视为长期静默 → 在缝隙内。
    if (this._lastDownlinkAudioTs === 0) return true;
    return now - this._lastDownlinkAudioTs >= this._gapMinSilenceMs;
  }

  // ── mic gate（半双工闸门，PRD §5 消自激）──
  get halfDuplexEnabled() {
    return this._halfDuplexGate === true;
  }
  setMicGated(gated) {
    this._micGated = gated;
  }
  get micGated() {
    return this._micGated;
  }
}
