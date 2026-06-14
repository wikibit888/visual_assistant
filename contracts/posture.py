"""契约七 · 坐姿守护事件（PRD §3.2 / §4.2，形态 = Pydantic）。

D 姿态守护 **100% 端侧**（MediaPipe Pose），云调用恒为 0。双条件（颈/背夹角 + 头部前伸）持续
`hunchback_hold_ms` 才触发；低头写字不误判（PRD §3.2）。D 的**唯一出口** = `posture.alert`：
绝不出声、绝不入工具表（push 事件，不是模型能拉的 function call，PRD §3 / §4.1）。

放行与择时（PRD §3.2.2 / §4.2）——确定性分三处，模型只碰最后一处：
  ① 检测：端侧 D。
  ② 放行 + 择时闸门：客户端（`mode==learning` 或 `active_problem!=null` 才放行；过 gap 静默闸门
     才注入；`reminder_count++`）。这一层挡住误触/抢话，是客户端确定性状态（PRD §4.1 蓝层）。
  ③ 措辞 + 最终择时：Live 模型（proactive audio：把「第二步」「第 3 次」缝进提醒 + 「最多等一句」
     软上界）。模型只决定怎么说、什么时候说出来。

故 `posture.alert` 只携带「发生了驼背」这一事实（单级 severity，不带话术）；severity 升级 / 提醒
次数 / 进度措辞都不在此——次数是客户端 `reminder_count`、措辞是模型的事。
"""

from pydantic import BaseModel, Field


class PostureAlert(BaseModel):
    """payload of `posture.alert` —— D（端侧）唯一出口。单级、不带话术。

    客户端发出前已过放行门控（mode/active_problem）与 gap 闸门；后端只负责把它作为一个 text 事件
    注入 Live 会话（推，非 function call），模型用 proactive 决定措辞与最终择时。
    """

    severity: str = Field("hunchback", description="v0.1 单级；不升级、不带话术")
    ts: int = Field(..., description="epoch 毫秒，端侧检测到的时刻")
