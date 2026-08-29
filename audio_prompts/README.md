# PCM 语音提示文件

这里放 16 kHz、单声道、S16_LE 的原始 PCM 文件，程序会用 `aplay` 播放：

```bash
aplay audio_prompts/presence.pcm -r 16000 -f S16_LE -c 1 -D plughw:Device
```

推荐文件名：

- `app_started.pcm`：智能门锁已启动
- `presence.pcm`：检测到有人靠近，请正对摄像头
- `auto_start.pcm`：检测到红外和人脸，开始自动认证
- `face_pass.pcm`：人脸识别通过
- `face_fail.pcm`：人脸识别失败，请正对摄像头
- `liveness_prompt.pcm`：请眨眼一次，并左右转头
- `liveness_pass.pcm`：活体检测通过
- `liveness_fail.pcm`：活体检测失败，请重新眨眼并转头
- `voice_prompt.pcm`：请说出口令：你好
- `voice_pass.pcm`：声纹识别通过
- `voice_fail.pcm`：声纹识别失败，请重新说出口令
- `auth_pass.pcm`：认证通过，欢迎回家
- `auth_fail.pcm`：认证失败，请重试
