"""模块 B（后端半）· 云 ASR/TTS 适配器（PRD §7.1）。

前端半在 web/src/modules/b_voice.js（VAD/PTT/播放队列/半双工 gate）。
后端只做云适配：asr_adapter（语音→asr.final）、tts_adapter（tts.say→音频）。
契约二语义：tts.stop = 立即停+清队列+ack。供应商默认 gemini 生态（config.roles）。
"""
