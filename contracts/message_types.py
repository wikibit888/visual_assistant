"""契约一辅助 · 总线消息类型 / 通道枚举（PRD §7.1 / §7.7）。

`type` 采用点分命名；`channel` 是粗路由标签。两者都进信封（envelope.py）。
铁律（PRD §7.1）：`gap.open` 只由 A 广播；`tts.*` 只发 B；D 只输出 `posture.alert`。
"""

from enum import Enum


class Channel(str, Enum):
    """粗路由标签——决定消息归哪条总线分支。"""

    VOICE = "voice"            # B 语音 I/O
    VISION = "vision"          # C 视觉服务
    WEATHER = "weather"        # weather.get 工具
    POSTURE = "posture"        # D 姿态守护（端侧）
    CONTROL = "control"        # 打断 / 控制
    ORCHESTRATOR = "orchestrator"  # A 编排核心（gap 等）


class MessageType(str, Enum):
    """全量总线消息类型。新增类型必须同步 CONTRACTS.md + fixtures。"""

    # —— 契约二 · 语音 I/O ——
    ASR_FINAL = "asr.final"        # B → A   payload: voice.AsrFinal
    TTS_SAY = "tts.say"            # A → B   payload: voice.TtsSay
    TTS_STOP = "tts.stop"          # A → B   payload: voice.TtsStop（立即停+清队列+ack）
    TTS_ACK = "tts.ack"            # B → A   payload: voice.TtsAck

    # —— 契约三 · 视觉（工具往返上总线）——
    VISION_REQUEST = "vision.request"   # A → C   payload: {kind, hint?}
    VISION_RESULT = "vision.result"     # C → A   payload: vision.*Result

    # —— 天气工具 ——
    WEATHER_REQUEST = "weather.request"  # A → weather  payload: weather.WeatherGetArgs
    WEATHER_RESULT = "weather.result"    # weather → A  payload: weather.WeatherResult

    # —— §3.2.2 · 姿态守护（D 唯一出口，端侧；不入 agent loop）——
    POSTURE_ALERT = "posture.alert"      # D → A(护栏层)  payload: state_machine.PostureAlert

    # —— 契约四 · 间隙仲裁（A 唯一广播）——
    GAP_OPEN = "gap.open"                # A → all  payload: state_machine.GapOpen

    # —— 控制面 · 建连配置下发（A 唯一下发；control 通道）——
    CONFIG_PUSH = "config.push"          # A → web  payload: config_push.ConfigPushPayload
