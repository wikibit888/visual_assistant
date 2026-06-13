// 模块 D · 坐姿守护（PRD §3.2 / §3.2.2 / §7.1）。M0 骨架。
// 100% 端侧（MediaPipe Pose），云成本恒为 0。**只发 posture.alert，绝不出声、绝不入 agent loop。**
// 双条件触发（胸椎后凸角度 + 头部位置/与桌面距离），持续 hunchback_hold_ms 才发。
// 放行/择机插入由 A 护栏层裁（§3.2.2）；D 不读 planner、不做话术。
// 演示口径：§9 由演示者导演触发（主动明显驼背），不依赖自动触发。
// 阈值（hunchback_hold_ms / thoracic_kyphosis_deg / head_forward_ratio）由 config 下发。

export function initPosture(/* ws, cfg */) {
  throw new Error("M1：MediaPipe Pose 双条件检测 → posture.alert");
}
