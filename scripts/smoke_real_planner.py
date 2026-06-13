"""真机冒烟 · 真 planner（deepseek）开放对话决策。

手动跑（在仓库根）：
    uv run python scripts/smoke_real_planner.py

它测什么：M2-01/M2-02 接的**真 deepseek planner**——发几条不同意图的话，看真模型
返回的结构化决策（kind / mode / tools / text）是否符合预期：
  - 开放问答 → kind=answer、mode=open、给简洁答案
  - 越界（点外卖等无执行类能力）→ 诚实「帮不上」（OPEN_STYLE/铁律口径）
  - 学习信号（看题）→ kind=tool_calls、read_problem、mode=learning
  - 任何输入都不崩、不落死分支

它不测什么（当前真实路径仍 NotImplementedError，故隔离）：
  - 真实云 ASR/TTS（语音进/出）——本冒烟纯文本进、看决策文本出，不走麦克风/扬声器。
  - 抓帧→视觉：用 call_planner 只取决策、不分发工具，不触真 gemini。

前置：把 DEEPSEEK_API_KEY 填进仓库根 .env（load_config 会自动 load .env）。
"""

import os

# 必须在 import 业务模块前设好 MOCK 开关：本冒烟要真 planner（关 MOCK_PLANNER/MOCK_LLM）。
# load_dotenv(override=False) 不会覆盖这里已设的值，故这三行稳定生效。
os.environ["MOCK_PLANNER"] = "0"   # 真 planner（不走固定脚本）
os.environ["MOCK_LLM"] = "0"
os.environ["MOCK_VISION"] = "1"    # 仅当后续改用 run_turn 跑全链路时隔离视觉；call_planner 本身不分发工具

import asyncio
import time

from contracts.config_schema import load_config
from contracts.mock import is_mock
from contracts.orchestration import PlannerKind
from server.a_core import orchestrator

cfg = load_config()  # 同时 load_dotenv() → 读 .env 的 DEEPSEEK_API_KEY

# ⚠ 冒烟把软超时放宽，好看清真模型实际输出与时延；
#    生产仍是 config.yaml 的 planner_timeout_ms=800（超时→维持现场景）。
cfg["roles"]["planner"]["planner_timeout_ms"] = 15000

if is_mock("MOCK_PLANNER") or is_mock("MOCK_LLM"):
    raise SystemExit("MOCK_PLANNER/MOCK_LLM 仍开着，跑的不是真 planner。检查 .env / 环境变量。")
if not os.getenv("DEEPSEEK_API_KEY"):
    raise SystemExit("DEEPSEEK_API_KEY 未设：填到仓库根 .env 再跑。")

PROMPTS = [
    "珠穆朗玛峰大概有多高？",     # 开放问答 → 期望 answer/open + 简洁答案
    "帮我点一份外卖",            # 越界 → 期望诚实「帮不上」
    "你能帮我做些什么？",        # 期望管理 → 坦诚能力边界
    "我有点烦，不想学了",        # 共情兜底 → 不落死分支
    "这道题我不会，你看看",      # 学习信号 → 期望 tool_calls/read_problem + learning
]


async def main() -> None:
    p = cfg["roles"]["planner"]
    print(f"真 planner = {p['provider']}/{p['model']}（冒烟软超时放宽至 15s；生产 800ms）\n")
    for text in PROMPTS:
        t0 = time.perf_counter()
        plan = await orchestrator.call_planner({"text": text, "current_mode": None}, cfg)
        ms = int((time.perf_counter() - t0) * 1000)
        tools = [t.name.value for t in plan.tools]
        # 真调用失败/超时会兜底成 answer + 空 text（_maintain_scene）：在此提示。
        suspect = " ⚠空text（疑真调用失败/超时，查 key/网络）" if (
            plan.kind == PlannerKind.ANSWER and not plan.text
        ) else ""
        print(f"用户：{text}")
        print(f"  决策：kind={plan.kind.value} mode={plan.mode.value} tools={tools} [{ms}ms]{suspect}")
        print(f"  text：{plan.text}\n")
    print("读法：开放问答应 answer/open 有答案；点外卖应诚实帮不上；看题应 tool_calls/read_problem/learning。")
    print(f"时延参考：若每条都明显 >800ms，说明生产 800ms 软超时会频繁兜底，值得回头评估该阈值。")


if __name__ == "__main__":
    asyncio.run(main())
