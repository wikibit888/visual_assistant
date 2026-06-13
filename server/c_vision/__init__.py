"""模块 C · 视觉服务（PRD §7.1 / §7.3）。

实现 vision.* 工具（read_problem/check_draft/observe）+ 重试与预算配合护栏。
供应商默认 gemini-2.5-flash（config.roles.vision）。MOCK_VISION=1 读 tests/fixtures/。
重试上限 = config.orchestration.vision_retry_max；预算由 A 护栏统一裁（C 不自管预算）。
"""
