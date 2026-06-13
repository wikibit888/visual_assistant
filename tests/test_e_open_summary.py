"""M2-05 验收：E 开放对话 prompt（OPEN_STYLE）+ 口头小结 prompt（SUMMARY_STYLE）。

只依赖 e_skills，零外部服务/零 LLM（E 是静态话术字符串，MOCK_LLM 口径下天然零依赖）。覆盖：
- 两段为非空措辞字符串（PRD §5.4 开放对话保障合同 / §3.1 S1-06 口头小结）；
- OPEN_STYLE 立住诚实兜底（帮不上 / 看不清 / 不编造）+ 期望管理坦诚；
- SUMMARY_STYLE 给出「做题数 + 坐姿提醒」结构，且不写死具体数字（数字运行时由工作记忆注入）；
- 两段均无模型名 / 无裸阈值（照 test_e_prompts.py 的 no-model-name / no-hardcoded-threshold 风格）；
- E 不内嵌路由：自由度只在措辞，prompts 模块无新增意图分类分支（铁律4）。
"""

import re

from server.e_skills import prompts


def test_open_and_summary_are_nonempty_strings():
    # 占位的 "" 必须被本卡填实，且是给 LLM 的措辞字符串（不是 None/列表/数字）。
    assert isinstance(prompts.OPEN_STYLE, str) and prompts.OPEN_STYLE.strip()
    assert isinstance(prompts.SUMMARY_STYLE, str) and prompts.SUMMARY_STYLE.strip()


def test_open_style_states_honest_fallback():
    # PRD §5.4 安全合同①：看不清 / 帮不上 → 诚实说明，绝不编造。
    assert "帮不上" in prompts.OPEN_STYLE, "越界兜底口径『帮不上』须在场"
    assert "看不清" in prompts.OPEN_STYLE, "看不清场景的诚实兜底须在场"
    assert "编造" in prompts.OPEN_STYLE, "『绝不编造』红线须在场"


def test_open_style_states_expectation_management():
    # PRD §5.4 安全合同②③：期望管理坦诚 + 不承诺执行类能力。
    assert "不保证" in prompts.OPEN_STYLE or "不夸大" in prompts.OPEN_STYLE, "期望管理坦诚措辞须在场"
    # 不假装有执行类能力（订外卖 / 控制设备之类）——任举其一证明边界在场。
    assert "执行类" in prompts.OPEN_STYLE or "外卖" in prompts.OPEN_STYLE


def test_summary_style_has_problem_count_and_posture_concepts():
    # PRD §3.1 S1-06 口头小结结构 = 做了几道题 + 卡点/错处 + 提醒坐姿几次。
    s = prompts.SUMMARY_STYLE
    assert ("做了几道" in s) or ("几道题" in s) or ("做题" in s), "小结须含『做题数』概念"
    assert "坐姿" in s, "小结须含『坐姿』概念"
    assert "提醒" in s, "小结须含『提醒（次数）』概念"


def test_summary_style_does_not_hardcode_specific_counts():
    # 铁律：具体数字（题数 / 提醒次数）运行时由工作记忆（mistake_log / reminder_count）注入，
    # 模板只给结构与措辞，不得写死任何具体次数（如「做了 3 道」「提醒了 2 次」）。
    s = prompts.SUMMARY_STYLE
    assert not re.search(r"做了\s*[0-9]+\s*道", s), "做题数不应在模板里写死具体数字"
    assert not re.search(r"提醒(?:了|过)?\s*[0-9]+\s*次", s), "坐姿提醒次数不应在模板里写死具体数字"


def test_open_and_summary_have_no_model_name():
    # 契约七：话术里禁硬编码供应商/模型名。
    for text in (prompts.OPEN_STYLE, prompts.SUMMARY_STYLE):
        low = text.lower()
        for banned in ("deepseek", "gemini", "openai", "gpt-"):
            assert banned not in low, f"话术不应硬编码模型名: {banned}"


def test_open_and_summary_have_no_hardcoded_threshold():
    # 契约七 / 铁律5：数值上限由 A 护栏裁决，话术里不写死阈值/次数。
    for text in (prompts.OPEN_STYLE, prompts.SUMMARY_STYLE):
        assert "0.6" not in text, "不应出现 confidence_gate 等具体阈值"
        # 澄清/工具轮次上限式写死（与 test_e_prompts.py 同款定性断言）。
        assert not re.search(r"澄清.{0,6}1\s*次", text)
        assert not re.search(r"最多\s*[0-9]+\s*轮", text)


def test_open_and_summary_are_constants_not_routers():
    # 铁律4：E 只供措辞，不做意图分类——两段恒等、不随调用/输入变化（非空、可重复读取）。
    assert prompts.OPEN_STYLE == prompts.OPEN_STYLE
    assert prompts.SUMMARY_STYLE == prompts.SUMMARY_STYLE
