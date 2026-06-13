"""M2-02 验收：开放对话「全交 LLM」接入 OPEN_STYLE（诚实兜底 / 期望管理 / 优雅收口）。

完全离线（零真实网络 / 零 LLM，契约六）：
- E 组合：prompts.planner_system_with_open_style() 同时含 planner 要素（工具名 + 越界「帮不上」）
  与 OPEN_STYLE 标志性要素（无预设 / 期望管理 / 优雅收口），且无模型名 / 无裸阈值（照 test_e_prompts 风格）。
- A 接线（录制断言）：自带 OpenAI 兼容假客户端，monkeypatch orchestrator.client_for_role，
  unset MOCK_PLANNER/MOCK_LLM 走真路径 → 断言录到的 system content 含 OPEN_STYLE 标志性子串
  （证明开放对话风格已进 planner 上下文），全程零网络。
- 开放对话基座跑通 / 不落死分支：MOCK_PLANNER+MOCK_VISION 下 run_turn 对普通闲聊与「越界」式
  输入都恰产一条 tts.say、text 非空（编排 loop 结构上不落死分支）。
- 诚实兜底口径在场：「帮不上」已在 planner_system_with_open_style() 上下文（越界→帮不上）。

铁律4（E 不内嵌路由）：开放对话「全交 LLM」——本卡只补「答复风格」字符串，不新增任何意图分类/
路由分支；planner 自己答。run_turn / call_planner 是协程，用 asyncio.run 驱动（与既有测试一致）。
"""

import asyncio
import re

import pytest

from contracts.voice import AsrFinal, TtsSay
from contracts.orchestration import PlannerOutput
from server.a_core import orchestrator
from server.a_core.working_memory_store import WorkingMemoryStore
from server.e_skills import prompts


# ── 自带 OpenAI 兼容假客户端（与 test_orchestrator_planner 同款，本文件自持，不 import 其私有夹具）──


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    """捕获 create() 调用参数的假 completions（同步，与真 OpenAI SDK 同形），零网络。"""

    def __init__(self, content, recorder):
        self._content = content
        self._recorder = recorder

    def create(self, **kwargs):
        self._recorder.update(kwargs)
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content, recorder):
        self.completions = _FakeCompletions(content, recorder)


class _FakeOpenAIClient:
    """OpenAI 兼容假客户端：client.chat.completions.create(...) → 写死 JSON，无任何网络。"""

    def __init__(self, content, recorder):
        self.chat = _FakeChat(content, recorder)
        # 故意不设 .model：迫使 _real_planner_call 从 cfg.roles.planner.model 取（验证无硬编码）。


def _asr(text: str, turn_id: str = "t-000001") -> AsrFinal:
    return AsrFinal(text=text, confidence=0.9, turn_id=turn_id)


def _planner_cfg(timeout_ms: int = 800) -> dict:
    """注入式最小 config：含 roles.planner（供假客户端路径成立）+ max_tool_rounds（run_turn 用）。"""
    return {
        "orchestration": {"max_tool_rounds": 2},
        "rails": {"enabled": False, "forced_tool_sequence": {}},
        "roles": {
            "planner": {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "temperature": 0,
                "structured_output": True,
                "planner_timeout_ms": timeout_ms,
            }
        },
        "providers": {"deepseek": {"api_base": "http://example.invalid", "api_key_env": "X"}},
    }


@pytest.fixture
def real_path(monkeypatch):
    """强制走「真 planner」分支：清掉 MOCK_PLANNER 与 MOCK_LLM（避免 provider 工厂回落桩）。

    真实网络由用例 monkeypatch 假客户端拦截，绝不外发。
    """
    monkeypatch.delenv("MOCK_PLANNER", raising=False)
    monkeypatch.delenv("MOCK_LLM", raising=False)


# ── 1) E 组合：planner 要素 + OPEN_STYLE 要素同在，且无模型名 / 无裸阈值 ───────────


def test_combined_contains_planner_and_open_style_elements():
    """planner_system_with_open_style() 同含 planner 要素（工具名 + 帮不上）与 OPEN_STYLE 标志性要素。"""
    combined = prompts.planner_system_with_open_style()
    # planner 要素：白名单工具名 + 越界「帮不上」诚实兜底口径。
    assert "read_problem" in combined, "planner 工具白名单要素须在场"
    assert "帮不上" in combined, "越界→『帮不上』诚实兜底口径须在 planner 上下文"
    # OPEN_STYLE 标志性要素（仅 OPEN_STYLE 有、PLANNER_SYSTEM 无，证明开放对话风格确已并入）。
    for marker in ("无预设", "期望管理", "优雅收口"):
        assert marker in combined, f"OPEN_STYLE 要素 {marker} 须并入 planner 上下文"


def test_combined_is_superset_of_planner_and_open_style():
    """组合 = PLANNER_SYSTEM + 连接段 + OPEN_STYLE：两段原文都被完整包含（措辞/组合归 E）。"""
    combined = prompts.planner_system_with_open_style()
    assert prompts.PLANNER_SYSTEM in combined
    assert prompts.OPEN_STYLE in combined


def test_combined_has_no_model_name_or_hardcoded_threshold():
    """契约七：组合后的 prompt 仍禁硬编码模型名 / 裸阈值（数值由 A 护栏裁决，铁律5）。"""
    combined = prompts.planner_system_with_open_style()
    low = combined.lower()
    for banned in ("deepseek", "gemini", "openai", "gpt-"):
        assert banned not in low, f"组合 prompt 不应硬编码模型名: {banned}"
    assert "0.6" not in combined, "不应出现 confidence_gate 等具体阈值"
    # 澄清/工具轮次上限式写死（与 test_e_prompts.py 同款定性断言）。
    assert not re.search(r"澄清.{0,6}1\s*次", combined)
    assert not re.search(r"最多\s*[0-9]+\s*轮", combined)


def test_combined_states_honest_fallback_no_execution():
    """诚实兜底口径在场：越界→『帮不上』指令已在 planner 上下文（不假装有执行类能力）。"""
    combined = prompts.planner_system_with_open_style()
    assert "帮不上" in combined
    assert "执行类" in combined or "外卖" in combined


def test_combined_is_constant_not_a_router():
    """铁律4：组合函数纯措辞拼接、恒等，不依赖任何输入分支（E 不做意图分类/路由）。"""
    assert prompts.planner_system_with_open_style() == prompts.planner_system_with_open_style()


# ── 2) A 接线（录制断言）：真路径 system content 含 OPEN_STYLE 标志性子串 ──────────


def test_real_planner_system_carries_open_style(real_path, monkeypatch):
    """注入假客户端，走真路径 → 录到的 system content 含 OPEN_STYLE 标志性子串（开放风格已进上下文）。

    全程零网络（假客户端返回写死 JSON）。证明 A 的 _real_planner_call 已改用 E 的组合 system。
    """
    recorder: dict = {}
    canned = '{"kind":"answer","mode":"open","tools":[],"text":"我在的，说说看。"}'

    def _fake_factory(role, cfg):
        assert role == "planner"
        return _FakeOpenAIClient(canned, recorder)

    monkeypatch.setattr(orchestrator, "client_for_role", _fake_factory)

    plan = asyncio.run(orchestrator.call_planner({"text": "在吗"}, cfg=_planner_cfg()))
    assert isinstance(plan, PlannerOutput)

    # 取录到的 system message content：必含 OPEN_STYLE 标志性子串（仅 OPEN_STYLE 独有）。
    messages = recorder["messages"]
    system_msg = next(m for m in messages if m["role"] == "system")
    system_content = system_msg["content"]
    for marker in ("无预设", "期望管理", "优雅收口"):
        assert marker in system_content, f"planner system 未携带 OPEN_STYLE 要素 {marker}"
    # 同时仍保留 planner 自身要素（工具白名单 + 帮不上），证明是「叠加」而非「替换」。
    assert "read_problem" in system_content
    assert "帮不上" in system_content
    # user message 仍是本回合识别文本（接线只改 system，不动 user）。
    user_msg = next(m for m in messages if m["role"] == "user")
    assert user_msg["content"] == "在吗"


def test_real_planner_system_equals_e_composition(real_path, monkeypatch):
    """更强断言：录到的 system content 恰等于 E 的 planner_system_with_open_style()（A 不自拼文本）。"""
    recorder: dict = {}
    canned = '{"kind":"answer","mode":"open","tools":[],"text":"好。"}'
    monkeypatch.setattr(
        orchestrator, "client_for_role", lambda role, cfg: _FakeOpenAIClient(canned, recorder)
    )

    asyncio.run(orchestrator.call_planner({"text": "随便聊聊"}, cfg=_planner_cfg()))
    system_msg = next(m for m in recorder["messages"] if m["role"] == "system")
    assert system_msg["content"] == prompts.planner_system_with_open_style()


# ── 3) 开放对话基座跑通 / 不落死分支（MOCK_PLANNER+MOCK_VISION，全离线）───────────


@pytest.fixture
def mocks(monkeypatch):
    """MOCK_PLANNER + MOCK_VISION：脱依赖、可独立空跑（契约六，全离线零网络）。"""
    monkeypatch.setenv("MOCK_PLANNER", "1")
    monkeypatch.setenv("MOCK_VISION", "1")


def _cfg() -> dict:
    return {
        "orchestration": {"max_tool_rounds": 2},
        "rails": {"enabled": False, "forced_tool_sequence": {}},
    }


def test_open_chitchat_yields_exactly_one_nonempty_tts(mocks):
    """普通闲聊（开放对话）→ 恰一条 tts.say、text 非空（基座跑通、不落死分支）。"""
    out = asyncio.run(
        orchestrator.run_turn(_asr("今天心情不错"), WorkingMemoryStore(), cfg=_cfg())
    )
    assert isinstance(out, list) and len(out) == 1
    assert isinstance(out[0], TtsSay)
    assert out[0].text.strip(), "开放对话回复 text 不应为空"
    assert out[0].turn_id == "t-000001"


def test_out_of_scope_request_still_yields_one_tts(mocks):
    """「越界」式输入（订外卖之类执行类）→ 编排 loop 仍恰产一条 tts.say（结构上不落死分支）。

    （离线只验「不死分支」；越界→『帮不上』的真实措辞需真机 + DEEPSEEK_API_KEY 验，见总结。）
    """
    out = asyncio.run(
        orchestrator.run_turn(_asr("帮我点外卖"), WorkingMemoryStore(), cfg=_cfg())
    )
    assert isinstance(out, list) and len(out) == 1
    assert isinstance(out[0], TtsSay)
    assert out[0].text.strip(), "越界请求也应有一条非空兜底回复，不留死分支"


def test_open_dialogue_no_dead_branch_across_varied_inputs(mocks):
    """多样输入（闲聊 / 越界 / 空泛）逐一都恰产一条非空 tts.say——开放对话「接得住任意请求」。"""
    for text in ("讲个笑话", "帮我控制空调", "嗯……", "你是谁"):
        out = asyncio.run(
            orchestrator.run_turn(_asr(text, turn_id="t-000009"), WorkingMemoryStore(), cfg=_cfg())
        )
        assert len(out) == 1 and isinstance(out[0], TtsSay)
        assert out[0].text.strip(), f"输入 {text!r} 落到了空回复（死分支）"


# ── 4) 诚实兜底口径在场（独立直断，呼应卡片验收）──────────────────────────────


def test_honest_fallback_phrase_present_in_planner_context():
    """『帮不上』出现在 planner_system_with_open_style()（越界→帮不上 指令已在 planner 上下文）。"""
    assert "帮不上" in prompts.planner_system_with_open_style()
