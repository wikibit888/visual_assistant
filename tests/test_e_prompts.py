"""M1-10 验收：E planner system prompt + 引导话术（结构化约束）。

只依赖 contracts(pydantic) + e_skills，零外部服务/零 LLM（MOCK_LLM 口径）。覆盖：
- planner 输出符合 PlannerOutput schema（MOCK_LLM 固定输出 + prompt 内嵌示例均可解析）；
- 工具白名单生效（prompt 枚举全部白名单；无模型名/裸阈值；越界兜底口径在场）；
- E 不内嵌路由（mock 输出恒定、不依赖输入；E 模块无意图分类分支）。
"""

import json
import re

from contracts.orchestration import Mode, PlannerKind, PlannerOutput, ToolName
from server.e_skills import prompts


def test_mock_planner_output_is_schema_valid_quickpath():
    out = prompts.mock_planner_output()
    assert isinstance(out, PlannerOutput)
    PlannerOutput.model_validate(out.model_dump())          # 构造 → 序列化 → 再校验
    assert out.kind == PlannerKind.ANSWER and out.tools == []  # 快路径、零工具


def test_mock_planner_output_is_constant_not_a_router():
    # 铁律4：E 不做意图分类——固定工厂多次调用恒等，不依赖任何输入分支。
    assert prompts.mock_planner_output() == prompts.mock_planner_output()


def test_planner_system_lists_every_whitelisted_tool():
    sys = prompts.PLANNER_SYSTEM
    for name in ToolName:
        assert name.value in sys, f"白名单工具 {name.value} 未出现在 planner prompt"


def test_planner_system_enumerates_all_kinds_and_modes():
    sys = prompts.PLANNER_SYSTEM
    for kind in PlannerKind:
        assert kind.value in sys, f"kind={kind.value} 未在 prompt 枚举"
    for mode in Mode:
        assert mode.value in sys, f"mode={mode.value} 未在 prompt 枚举"


def test_planner_system_has_no_model_name_or_hardcoded_threshold():
    # 契约七：prompt 禁硬编码模型名/阈值（阈值由 A 护栏裁决，铁律5）。
    sys_lower = prompts.PLANNER_SYSTEM.lower()
    for banned in ("deepseek", "gemini", "openai", "gpt-"):
        assert banned not in sys_lower, f"prompt 不应硬编码模型名: {banned}"
    assert "0.6" not in prompts.PLANNER_SYSTEM         # confidence_gate 等具体阈值
    # 澄清/工具轮次上限不写死次数，应是定性措辞
    assert not re.search(r"澄清.{0,6}1\s*次", prompts.PLANNER_SYSTEM)
    assert not re.search(r"最多\s*[0-9]+\s*轮", prompts.PLANNER_SYSTEM)


def test_planner_system_states_no_execution_capability():
    # 越界 → 统一「帮不上」诚实兜底口径（PRD §1.5 / §5.4）必须在 prompt 立住。
    assert "帮不上" in prompts.PLANNER_SYSTEM


def test_planner_system_embedded_examples_parse_to_schema():
    # 给 LLM 的内嵌 JSON 示例本身必须 schema 合规（否则等于教坏 LLM）。
    examples = re.findall(r'\{"kind".*\}', prompts.PLANNER_SYSTEM)
    assert examples, "未在 prompt 中找到内嵌 JSON 示例"
    for raw in examples:
        PlannerOutput.model_validate(json.loads(raw))     # 解析 + 校验双通过
    # 至少覆盖三种 kind，证明 prompt 把快路径/工具/澄清都示范到了
    kinds = {PlannerOutput.model_validate(json.loads(r)).kind for r in examples}
    assert {PlannerKind.ANSWER, PlannerKind.TOOL_CALLS, PlannerKind.CLARIFY} <= kinds


def test_guide_style_three_level_ladder_present():
    for level in ("方向级", "操作级", "示范级"):
        assert level in prompts.GUIDE_STYLE, f"引导阶梯缺 {level}"


def test_posture_templates_nonempty_short_zero_llm():
    tpls = prompts.POSTURE_TEMPLATES
    assert tpls, "坐姿话术模板不应为空"
    assert all(isinstance(t, str) and t.strip() for t in tpls)
    assert all(len(t) <= 30 for t in tpls), "gap 窗口 1s，话术须短"
