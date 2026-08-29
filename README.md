# Jetson 智能门锁 MVP 使用说明

这是一个在 NVIDIA Jetson Nano 上验证的智能门锁 MVP，当前包含：

- 串口红外人体传感器
- 单个 RGB 摄像头实时画面
- InsightFace/ArcFace 人脸身份识别和人脸库采集
- MediaPipe 眨眼/左右转头活体检测
- SpeechBrain ECAPA 声纹注册和识别
- 自动认证状态机
- `aplay` PCM 语音引导
- PySide6 图形界面
- 继电器开锁接口

调试版本默认使用 `--no-unlock`，不会真正驱动继电器开锁。

## 1. 进入工程目录

在 Jetson 终端运行：

```bash
cd ~/smart_lock_ai_20260829_1915
```

## 2. 启动界面

直接在 Jetson 桌面上运行：

```bash
./run_gui.sh
```

如果通过 SSH 启动并显示到 Jetson 本机屏幕：

```bash
DISPLAY=:0 ./run_gui.sh
```

`run_gui.sh` 默认执行：

```bash
python3 gui.py --hardware --no-unlock
```

`--no-unlock` 表示禁止真实开锁。现场调试阶段建议一直保留。

## 3. 界面怎么用

1. 点击 `启动`。
2. 程序会持续打开摄像头、人脸识别和红外检测。
3. 当红外检测到人体靠近，并且人脸识别通过时，会自动开始认证。
5. 自动认证流程会依次显示：
   - `1. 红外检测`
   - `2. 人脸识别`
   - `3. 活体检测`
   - `4. 口令+声纹`
   - `5. 融合结果`
5. 活体检测时请眨眼一次，并左右转头。
6. 声纹检测时请说界面提示的口令，默认是 `你好`。

也可以点击 `手动认证` 强制跑一遍完整流程。

## 4. 注册人脸库

1. 点击 `启动`。
2. 在姓名输入框输入姓名，例如 `赵长鹏`。
3. 正面对准摄像头。
4. 点击 `采集人脸`。
5. 稍微改变距离和角度，再采集几张。

建议每个人采集 3-8 张。

保存位置：

```text
database/faces/姓名/
database/face_embeddings/姓名/
```

InsightFace 使用 embedding 做身份识别，不需要传统训练步骤。`刷新人脸库` 主要用于刷新界面统计。

## 5. 注册声纹库

1. 在姓名输入框输入和人脸一致的姓名。
2. 语音口令输入框可留空，默认使用 `config.yaml` 里的 `speaker.passphrase`。
3. 点击 `采集声纹`。
4. 听到或看到提示后，说出口令，例如 `你好`。
5. 建议同一个人采集 3 次左右。

保存位置：

```text
database/voices/姓名/
```

当前声纹后端是 SpeechBrain ECAPA。认证时会要求说固定口令，并对这段录音做声纹比对。注意：当前版本已经实现“固定口令引导 + 声纹识别”，但还没有接入语音转文字 ASR，所以它不会真正判断你说出的文字内容是不是 `你好`。后续可以接麦克风阵列串口语义结果或本地 ASR，把“口令文字识别”补成强校验。

## 6. 自动认证逻辑

默认开启 `自动认证：红外+人脸通过后自动开始`。

流程是：

```text
红外检测到靠近
  -> 摄像头持续识别人脸
  -> 红外通过 + 人脸通过
  -> 自动提示活体动作
  -> MediaPipe 活体检测
  -> 自动提示说口令
  -> SpeechBrain 声纹识别
  -> 检查人脸身份和声纹身份是否一致
  -> 多模态融合判定
```

自动认证冷却时间在 `config.yaml` 中配置：

```yaml
auto_auth:
  enabled: true
  cooldown_seconds: 8.0
```

## 7. 语音引导

程序会优先播放 `audio_prompts/` 下的原始 PCM 文件，命令格式与你验证过的一致：

```bash
aplay audio_prompts/voice_prompt.pcm -r 16000 -f S16_LE -c 1 -D plughw:Device
```

音频要求：

```text
采样率：16000
格式：S16_LE
声道：1
播放设备：plughw:Device
```

推荐放置这些文件：

```text
audio_prompts/app_started.pcm
audio_prompts/presence.pcm
audio_prompts/auto_start.pcm
audio_prompts/face_pass.pcm
audio_prompts/face_fail.pcm
audio_prompts/liveness_prompt.pcm
audio_prompts/liveness_pass.pcm
audio_prompts/liveness_fail.pcm
audio_prompts/voice_prompt.pcm
audio_prompts/voice_pass.pcm
audio_prompts/voice_fail.pcm
audio_prompts/auth_pass.pcm
audio_prompts/auth_fail.pcm
```

如果某个 PCM 文件不存在，程序不会报错退出，会尝试 `spd-say`/`espeak`，再不行就只写日志。

## 8. 主要配置

配置文件：

```text
config.yaml
```

红外传感器：

```yaml
sensor:
  type: serial_infrared
  serial:
    port_candidates:
      - "/dev/ttyUSB0"
      - "/dev/ttyUSB1"
    baudrate: 9600
    command_hex: "02 03 00 04 00 01 C5 F8"
```

声纹口令和麦克风：

```yaml
speaker:
  passphrase: "你好"
  input_device: 2
  sample_rate: 16000
  record_seconds: 2.5
```

语音播放：

```yaml
voice_feedback:
  enabled: true
  backend: aplay_pcm
  pcm_dir: "audio_prompts"
  pcm_device: "plughw:Device"
```

融合权重：

```yaml
fusion:
  threshold: 0.78
  weights:
    face: 0.45
    liveness: 0.25
    speaker: 0.20
    sensor: 0.10
```

## 9. 单独测试硬件

测试红外传感器：

```bash
python3 test_sensor.py --count 10 --verbose
```

无人时一般是：

```text
detected=False value=0
```

有人靠近时一般是：

```text
detected=True value=1
```

测试麦克风：

```bash
python3 test_audio.py --seconds 1
```

如果 `peak` 和 `rms` 大于 0，说明麦克风能录到声音。

测试语音提示播放：

```bash
python3 test_voice_prompt.py voice_prompt
```

它会优先播放：

```text
audio_prompts/voice_prompt.pcm
```

检查模型后端：

```bash
python3 check_runtime.py
```

期望看到：

```text
face_backend=InsightFaceAuthenticator
liveness_backend=MediaPipeLivenessChecker
speaker_backend=SpeechBrainSpeakerAuthenticator
speaker_model=ready
```

## 10. 真实开锁

调试阶段保持：

```bash
./run_gui.sh
```

不要勾选 `允许真实开锁`。

继电器、电磁锁、电源和现场安全确认无误后，再去掉 `--no-unlock` 或修改 `run_gui.sh`。真实开锁有物理安全风险，必须有人在门锁旁看护测试。
