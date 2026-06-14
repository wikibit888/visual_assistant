"""中继核心（relay）· Live 版的新脊梁。

塌进 Live 模型后，原编排核心 A 的 planner 循环 / 原语音 B 的自搓 VAD-打断-半双工都没了。
后端只剩两件确定性的事（PRD §1.4 / §4.1 绿层）：

  - live_bridge   —— 每个 WS 连接一座桥：客户端 ⇄ 后端 ⇄ Live 会话。泵音频双向、把客户端控制
                     事件翻译进 Live 会话、把 Live 输出翻译成客户端消息、派发 function_call。
  - tool_dispatch —— 工具执行体路由 + 视觉预算计数（确定性，模型碰不到）。

跨进程只走契约（contracts/）：本包对客户端只产/收 Envelope + 音频二进制帧。
"""
