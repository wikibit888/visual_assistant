"""工具执行体（tools）· 确定性绿层（PRD §1.4 / §3 / §5）。

Live 模型发 function_call，这里是「怎么做到」的确定性代码：
  - vision_tools —— look_at_page / check_draft / observe：抓帧交 gemini 多模态识别 → 带 confidence 的
                    结构化结果；MOCK_VISION=1 读 fixture。绝不编造（低置信如何措辞 = 提示词约束）。
  - weather      —— weather_get：定位 → Open-Meteo → 缓存/写死兜底；MOCK_WEATHER=1 返回兜底。

工具只返回结构化结果（confidence 原样），不自决是否/如何向用户说——那是 Live 模型 + 提示词的事。
"""
