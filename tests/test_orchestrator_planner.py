"""M2-01 验收：A 编排循环接真 planner（deepseek 温度0+结构化）+ 快路径 + 软超时。

完全离线（零真实网络）：
- MOCK_PLANNER=1 路径已由 test_orchestrator_skeleton.py 覆盖，这里专测**非 MOCK 真路径**。
- 真路径用 monkeypatch 替换 `_real_planner_call`（慢协程触发超时 / 返回固定 PlannerOutput），
  或注入一个 OpenAI 兼容的假客户端（返回写死 JSON，无网络），断言温度/结构化/超时取自 cfg。

验证（对齐卡片验收）：
  (a) 超时 → 维持现场景（零工具、一条 tts.say、不抛异常）。
  (b) 真实解析：_real_planner_call 返回合规 PlannerOutput → call_planner 原样返回。
  (c) 快路径：answer kind → tools 为空、不 dispatch 工具。
  (d) planner_timeout_ms / temperature 取自注入 cfg（改 cfg 值改变行为 / 被读取），证明无硬编码。
  + 维持现场景 mode sticky（沿用 current_mode）；MOCK_PLANNER 短路不读 cfg、不碰真路径；
    planner 不直产 TTS（仍经护栏闸门收口为 TtsSay，铁律3）。

run_turn / call_planner 是协程；无 pytest-asyncio，用 asyncio.run 驱动（与既有测试一致）。
"""

import asyncio

import pytest

from contracts.voice import AsrFinal, TtsSay
from contracts.orchestration import (
    Mode,
    PlannerKind,
    PlannerOutput,
    ToolCall,
    ToolName,
)
from contracts.working_memory import WorkingMemory
from server.a_core import orchestrator, dispatch


# ── 测试夹具 / 辅助 ───────────────────────────────────────────────────────────


@pytest.fixture
def real_path(monkeypatch):
    """强制走「真 planner」分支：清掉 MOCK_PLANNER（与 MOCK_LLM，避免 provider 工厂回落桩）。

    真实网络由各用例 monkeypatch `_real_planner_call` / 假客户端拦截，绝不外发。
    """
    monkeypatch.delenv("MOCK_PLANNER", raising=False)
    monkeypatch.delenv("MOCK_LLM", raising=False)


def _asr(text: str, turn_id: str = "t-000001") -> AsrFinal:
    return AsrFinal(text=text, confidence=0.9, turn_id=turn_id)


def _planner_cfg(timeout_ms: int = 800, temperature=0, structured: bool = True) -> dict:
    """注入式最小 config：被测代码从此 dict 读 planner_timeout_ms/temperature（禁硬编码，契约七）。

    含 max_tool_rounds 供 run_turn 用；roles.planner 给出 provider/model 以便假客户端路径成立。
    """
    return {
        "orchestration": {"max_tool_rounds": 2},
        "rails": {"enabled": False},
        "roles": {
            "planner": {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "temperature": temperature,
                "structured_output": structured,
                "planner_timeout_ms": timeout_ms,
            }
        },
        "providers": {"deepseek": {"api_base": "http://example.invalid", "api_key_env": "X"}},
    }


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


# ── (a) 软超时 → 维持现场景 ───────────────────────────────────────────────────


def test_timeout_maintains_scene_via_call_planner(real_path, monkeypatch):
    """注入极小 planner_timeout_ms + 慢 _real_planner_call → 超时兜底 answer/零工具，不抛异常。"""

    async def _slow(planner_input, cfg):
        await asyncio.sleep(5)  # 远超注入的 1ms 软超时
        raise AssertionError("超时应已触发，不该跑到这里")

    monkeypatch.setattr(orchestrator, "_real_planner_call", _slow)

    plan = asyncio.run(
        orchestrator.call_planner({"text": "你好", "current_mode": None}, cfg=_planner_cfg(timeout_ms=1))
    )
    assert isinstance(plan, PlannerOutput)
    assert plan.kind == PlannerKind.ANSWER  # 维持现场景：不发起工具
    assert plan.tools == []
    assert plan.text is None  # 交给 run_turn 落 FALLBACK_TEXT


def test_timeout_maintains_scene_via_run_turn_no_dispatch_one_tts(real_path, monkeypatch):
    """run_turn 真路径超时：不 dispatch 任何工具、返回恰一条 tts.say、不抛异常（稳定性第一）。"""

    async def _slow(planner_input, cfg):
        await asyncio.sleep(5)

    monkeypatch.setattr(orchestrator, "_real_planner_call", _slow)

    # 计数 dispatch：超时兜底是 answer，不应触发任何工具分发。
    calls = []
    real_dispatch = dispatch.dispatch

    async def _counting_dispatch(tool_call):
        calls.append(tool_call.name)
        return await real_dispatch(tool_call)

    monkeypatch.setattr(orchestrator.dispatch, "dispatch", _counting_dispatch)

    out = asyncio.run(
        orchestrator.run_turn(_asr("你好"), WorkingMemory(), cfg=_planner_cfg(timeout_ms=1))
    )
    assert calls == []  # 维持现场景，零工具分发
    assert isinstance(out, list) and len(out) == 1
    assert isinstance(out[0], TtsSay)
    assert out[0].text == orchestrator.FALLBACK_TEXT  # text=None → 护栏闸门收口为 FALLBACK
    assert out[0].turn_id == "t-000001"


def test_timeout_maintains_sticky_mode(real_path, monkeypatch):
    """维持现场景 mode 沿用上一回合：current_mode=learning → 兜底 PlannerOutput.mode=learning。"""

    async def _slow(planner_input, cfg):
        await asyncio.sleep(5)

    monkeypatch.setattr(orchestrator, "_real_planner_call", _slow)

    plan = asyncio.run(
        orchestrator.call_planner(
            {"text": "嗯", "current_mode": Mode.LEARNING.value}, cfg=_planner_cfg(timeout_ms=1)
        )
    )
    assert plan.mode == Mode.LEARNING  # sticky：维持现场景，不抖回 open


def test_exception_in_real_call_falls_back(real_path, monkeypatch):
    """非超时异常（网络/解析）同样兜底维持现场景，绝不抛给 run_turn。"""

    async def _boom(planner_input, cfg):
        raise RuntimeError("模拟网络/解析失败")

    monkeypatch.setattr(orchestrator, "_real_planner_call", _boom)

    plan = asyncio.run(
        orchestrator.call_planner({"text": "你好", "current_mode": None}, cfg=_planner_cfg())
    )
    assert plan.kind == PlannerKind.ANSWER
    assert plan.tools == []


# ── (b) 真实解析：_real_planner_call 返回合规 PlannerOutput → 原样返回 ─────────


def test_real_parse_returned_verbatim(real_path, monkeypatch):
    """monkeypatch _real_planner_call 返回固定合规 PlannerOutput → call_planner 原样返回。"""
    fixed = PlannerOutput(
        kind=PlannerKind.TOOL_CALLS,
        mode=Mode.LEARNING,
        tools=[ToolCall(name=ToolName.READ_PROBLEM)],
        text="我看看你指的这道题。",
    )

    async def _fixed(planner_input, cfg):
        return fixed

    monkeypatch.setattr(orchestrator, "_real_planner_call", _fixed)

    plan = asyncio.run(
        orchestrator.call_planner({"text": "看看这道题", "current_mode": None}, cfg=_planner_cfg())
    )
    assert plan is fixed
    assert plan.kind == PlannerKind.TOOL_CALLS
    assert [t.name for t in plan.tools] == [ToolName.READ_PROBLEM]


# ── (c) 快路径：answer kind → 零工具、不 dispatch ─────────────────────────────


def test_fast_path_answer_no_tools(real_path, monkeypatch):
    """answer kind → tools 为空；run_turn 不 dispatch 任何工具，直接出 tts.say（快路径）。"""

    async def _answer(planner_input, cfg):
        return PlannerOutput(kind=PlannerKind.ANSWER, mode=Mode.OPEN, tools=[], text="大约 8848 米。")

    monkeypatch.setattr(orchestrator, "_real_planner_call", _answer)

    calls = []
    real_dispatch = dispatch.dispatch

    async def _counting_dispatch(tool_call):
        calls.append(tool_call.name)
        return await real_dispatch(tool_call)

    monkeypatch.setattr(orchestrator.dispatch, "dispatch", _counting_dispatch)

    out = asyncio.run(
        orchestrator.run_turn(_asr("珠峰多高"), WorkingMemory(), cfg=_planner_cfg())
    )
    assert calls == []  # 快路径零工具
    assert len(out) == 1 and isinstance(out[0], TtsSay)
    assert out[0].text == "大约 8848 米。"  # answer 文本经护栏闸门（占位直通）→ tts.say


def test_call_planner_answer_tools_empty(real_path, monkeypatch):
    """直测 call_planner：answer kind 的 tools 必为空（快路径形态）。"""

    async def _answer(planner_input, cfg):
        return PlannerOutput(kind=PlannerKind.ANSWER, mode=Mode.OPEN, tools=[], text="好的。")

    monkeypatch.setattr(orchestrator, "_real_planner_call", _answer)
    plan = asyncio.run(orchestrator.call_planner({"text": "嗯"}, cfg=_planner_cfg()))
    assert plan.kind == PlannerKind.ANSWER
    assert plan.tools == []


# ── (d) planner_timeout_ms / temperature 取自注入 cfg（证明无硬编码）───────────


def test_temperature_and_structured_read_from_cfg(real_path, monkeypatch):
    """真正跑 _real_planner_call 体：注入假客户端，断言 create() 收到的温度/结构化来自 cfg。

    全程零网络（假客户端返回写死 JSON）。改 cfg.temperature 即改 create 入参，证明无硬编码。
    """
    recorder: dict = {}
    canned = '{"kind":"answer","mode":"open","tools":[],"text":"hi"}'

    def _fake_factory(role, cfg):
        assert role == "planner"
        return _FakeOpenAIClient(canned, recorder)

    monkeypatch.setattr(orchestrator, "client_for_role", _fake_factory)

    cfg = _planner_cfg(temperature=0, structured=True)
    plan = asyncio.run(orchestrator.call_planner({"text": "你好"}, cfg=cfg))

    # 解析成功，且 create 入参取自 cfg（温度0、JSON 结构化、模型名来自 cfg.roles.planner.model）。
    assert isinstance(plan, PlannerOutput) and plan.kind == PlannerKind.ANSWER
    assert recorder["temperature"] == 0
    assert recorder["response_format"] == {"type": "json_object"}
    assert recorder["model"] == "deepseek-chat"  # 模型名经 config（client_for_role），零硬编码


def test_temperature_value_follows_cfg(real_path, monkeypatch):
    """改 cfg.temperature → create() 收到的温度随之改变（坐实读 cfg、非写死 0）。"""
    recorder: dict = {}
    canned = '{"kind":"answer","mode":"open","tools":[],"text":"hi"}'
    monkeypatch.setattr(
        orchestrator, "client_for_role", lambda role, cfg: _FakeOpenAIClient(canned, recorder)
    )

    asyncio.run(orchestrator.call_planner({"text": "x"}, cfg=_planner_cfg(temperature=0.7)))
    assert recorder["temperature"] == 0.7


def test_structured_false_omits_response_format(real_path, monkeypatch):
    """structured_output=false（cfg）→ 不带 response_format，证明该开关取自 cfg。"""
    recorder: dict = {}
    canned = '{"kind":"answer","mode":"open","tools":[],"text":"hi"}'
    monkeypatch.setattr(
        orchestrator, "client_for_role", lambda role, cfg: _FakeOpenAIClient(canned, recorder)
    )

    asyncio.run(orchestrator.call_planner({"text": "x"}, cfg=_planner_cfg(structured=False)))
    assert "response_format" not in recorder


def test_generous_timeout_lets_fast_call_succeed(real_path, monkeypatch):
    """planner_timeout_ms 充裕（cfg）→ 快 _real_planner_call 不被超时打断，正常返回其结果。"""
    fixed = PlannerOutput(kind=PlannerKind.ANSWER, mode=Mode.OPEN, tools=[], text="ok")

    async def _fast(planner_input, cfg):
        return fixed

    monkeypatch.setattr(orchestrator, "_real_planner_call", _fast)
    plan = asyncio.run(orchestrator.call_planner({"text": "x"}, cfg=_planner_cfg(timeout_ms=5000)))
    assert plan is fixed  # 未触发超时兜底


def test_zero_timeout_runs_without_wait_for(real_path, monkeypatch):
    """planner_timeout_ms=0（cfg）→ 不套 wait_for，直接 await 真调用（边界：软超时关闭）。"""
    fixed = PlannerOutput(kind=PlannerKind.ANSWER, mode=Mode.OPEN, tools=[], text="ok")

    async def _fast(planner_input, cfg):
        return fixed

    monkeypatch.setattr(orchestrator, "_real_planner_call", _fast)
    plan = asyncio.run(orchestrator.call_planner({"text": "x"}, cfg=_planner_cfg(timeout_ms=0)))
    assert plan is fixed


# ── MOCK_PLANNER 短路：不读 cfg、不碰真路径（契约六）─────────────────────────


def test_mock_planner_short_circuits_without_touching_real_call(monkeypatch):
    """MOCK_PLANNER=1 → 走固定脚本，绝不调 _real_planner_call、绝不读 cfg（脱依赖，契约六）。"""
    monkeypatch.setenv("MOCK_PLANNER", "1")

    async def _must_not_call(planner_input, cfg):
        raise AssertionError("MOCK_PLANNER=1 不应触达真 planner")

    monkeypatch.setattr(orchestrator, "_real_planner_call", _must_not_call)

    # 不传 cfg（与既有 skeleton 测试同款调用）：MOCK 分支短路，不应因缺 cfg 报错。
    plan = asyncio.run(orchestrator.call_planner({"text": "看看这道题"}))
    assert plan.kind == PlannerKind.TOOL_CALLS
    assert plan.mode == Mode.LEARNING
    assert [t.name for t in plan.tools] == [ToolName.READ_PROBLEM]


def test_mock_planner_does_not_call_load_config(monkeypatch):
    """MOCK_PLANNER=1 短路：不调 load_config（不读磁盘 config），坐实脱依赖。"""
    monkeypatch.setenv("MOCK_PLANNER", "1")

    def _boom(*a, **k):
        raise AssertionError("MOCK 路径不应读 config")

    monkeypatch.setattr(orchestrator, "load_config", _boom)
    plan = asyncio.run(orchestrator.call_planner({"text": "你好"}))
    assert plan.kind == PlannerKind.ANSWER  # 固定脚本快路径


# ── 铁律：planner 不直产 TTS（仍经护栏闸门收口为 TtsSay，铁律3）──────────────


def test_planner_output_still_passes_guardrail_gate(real_path, monkeypatch):
    """真路径 answer → run_turn 出的是经 _guardrail_decide 收口的 TtsSay，planner 未绕过闸门。"""
    seen = {}
    real_gate = orchestrator._guardrail_decide

    def _spy_gate(text, memory, cfg):
        seen["text"] = text
        return real_gate(text, memory, cfg)

    monkeypatch.setattr(orchestrator, "_guardrail_decide", _spy_gate)

    async def _answer(planner_input, cfg):
        return PlannerOutput(kind=PlannerKind.ANSWER, mode=Mode.OPEN, tools=[], text="你好。")

    monkeypatch.setattr(orchestrator, "_real_planner_call", _answer)

    out = asyncio.run(orchestrator.run_turn(_asr("hi"), WorkingMemory(), cfg=_planner_cfg()))
    assert seen["text"] == "你好。"  # 候选文本必经护栏闸门（铁律3/5）
    assert isinstance(out[0], TtsSay)
