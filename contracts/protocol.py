"""契约一辅助 · WS 协议消息类型 / 通道枚举（PRD §4，客户端 ⇄ 后端）。

这是前后端并行开发的「接口字典」。`type` 点分命名；`channel` 是粗分组标签。
两类方向（仅文档约定，运行时不强校验方向）：
  - 客户端 → 后端：session.start/update · input.activity_* · frame.response · posture.alert · text.input
  - 后端 → 客户端：session.ready · config.push · transcript · tool.activity · interrupted ·
                    frame.request · error

注意：`vision.*` / `weather.*`（工具执行体的返回）**不在本 WS 协议面**——它们是后端
`function_call` 往返的内部结果（contracts/vision.py · weather.py 定义其 schema，供工具执行体与
fixture 校验用），不经客户端 WS。客户端只通过 `tool.activity` 感知「工具在动」。
"""

from enum import Enum


class Channel(str, Enum):
    """控制帧的粗分组标签（决定客户端/后端各自挂哪个 handler）。"""

    SESSION = "session"        # 会话生命周期（start/update/ready）
    AUDIO = "audio"            # 音频轮次控制（PTT 边界 / 打断）；音频本体走二进制帧
    TRANSCRIPT = "transcript"  # 字幕 + 工具活动提示（UI 展示）
    FRAME = "frame"            # 摄像头单帧请求/回传（视觉工具触发时）
    POSTURE = "posture"        # 坐姿守护（端侧推给后端 → 注入 Live 会话）
    CONTROL = "control"        # 配置下发 / 文字输入兜底 / 错误


class MessageType(str, Enum):
    """全量 WS 协议消息类型。新增类型必须同步 CONTRACTS.md + fixtures。"""

    # —— 会话生命周期（SESSION）——
    SESSION_START = "session.start"      # 客户端→后端：开 Live 会话  payload: session.SessionStart
    SESSION_UPDATE = "session.update"    # 客户端→后端：运行时切换    payload: session.SessionUpdate
    SESSION_READY = "session.ready"      # 后端→客户端：会话就绪      payload: session.SessionReady

    # —— 音频轮次控制（AUDIO）；音频本体是二进制帧，不在此枚举 ——
    INPUT_ACTIVITY_START = "input.activity_start"  # 客户端→后端：PTT 按下/开说  payload: {}
    INPUT_ACTIVITY_END = "input.activity_end"      # 客户端→后端：PTT 松手/说完  payload: {}
    INTERRUPTED = "interrupted"          # 后端→客户端：打断 → 停播+清队列  payload: audio.Interrupted

    # —— 字幕与工具活动（TRANSCRIPT）——
    TRANSCRIPT = "transcript"            # 后端→客户端：用户/助手转写  payload: transcript.Transcript
    TOOL_ACTIVITY = "tool.activity"      # 后端→客户端：工具在动（UI + 客户端置 active_problem）

    # —— 摄像头单帧往返（FRAME）——
    FRAME_REQUEST = "frame.request"      # 后端→客户端：要一帧  payload: frame.FrameRequest
    FRAME_RESPONSE = "frame.response"    # 客户端→后端：回一帧  payload: frame.FrameResponse

    # —— 坐姿守护（POSTURE）——
    POSTURE_ALERT = "posture.alert"      # 客户端(端侧 D)→后端：驼背事件  payload: posture.PostureAlert

    # —— 控制面（CONTROL）——
    CONFIG_PUSH = "config.push"          # 后端→客户端：前端阈值快照  payload: config_push.ConfigPushPayload
    TEXT_INPUT = "text.input"            # 客户端→后端：文字输入兜底（TTS/ASR 降级）  payload: control.TextInput
    ERROR = "error"                      # 后端→客户端：错误/降级提示  payload: control.ErrorEvent
