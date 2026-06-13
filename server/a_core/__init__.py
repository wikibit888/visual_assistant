"""模块 A · 编排核心（PRD §7.1 / §7.2，权属 = 编排循环 + 确定性护栏 + 工具注册表）。

子文件：
  orchestrator.py        反应式 agent 循环（感知→planner→dispatch→护栏→tts.say）
  dispatch.py            工具分发（含 rails forced_tool_sequence 注入 hook）
  guardrails.py          确定性护栏（置信门控/澄清上限/loop 上限/视觉预算/粘滞/答案护栏）
  gap_arbiter.py         轮次状态机 + 间隙仲裁（gap.open 唯一广播 + 姿态放行门控）
  tool_registry.py       工具注册表运行时（绑定 contracts.TOOL_REGISTRY → 真实/ MOCK 实现）
  rails.py               rails 切换（config.rails，与 agentic 同代码）
  working_memory_store.py 工作记忆运行时（契约十，仅内存）

铁律：planner 不得绕过护栏直产 TTS；护栏不可被提示词关闭；E 不得内嵌路由。
"""
