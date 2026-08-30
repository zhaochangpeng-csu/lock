# Jetson 智能门锁 MVP 使用说明

> 最终实现方案以 `docs/LLM_AGENT_PLAN.md`（v2.0）为准。
> 当前已落地：硬件融合认证后签发一次性短时凭证，由语音 Agent 在对话中请求开锁。
> 声纹识别为文本无关，不再要求固定口令。
> Pipecat 连续对话已在 WSL/Windows/Jetson 上完成验证；`voice_agent.py` 保留为固定时长单轮降级入口。
> 系统已支持模型预加载、Agent 预启动等待凭证、幂等监督启动。

## 项目能力

- 串口红外人体传感器（常驻轮询）
- 单个 RGB 摄像头实时画面
- InsightFace/ArcFace 人脸身份识别和人脸库采集
- MediaPipe 眨眼/左右转头活体检测
- SpeechBrain ECAPA 文本无关声纹注册和识别
- 自动认证状态机
- `aplay` PCM 语音引导
- PySide6 图形界面
- FastGPT 语音 Agent 与本地受控工具网关
- 继电器开锁接口

调试版本默认使用 `--no-unlock`，不会真正驱动继电器开锁。

## 1. 进入工程目录

在 Jetson 终端运行：

```bash
cd ~/smart_lock_ai_20260829_1915
```

## 2. 启动界面

推荐使用带进程监督和预加载的一键启动：

```bash
./run_smart_lock.sh
```

该脚本会：

- 启动本地工具网关并自动拉起；
- 预加载 FunASR，并让 Pipecat 语音 Agent 等待硬件认证凭证；
- 启动 GUI（点击“启动”后会预加载人脸/活体/声纹模型）；
- 自动监督网关、Agent、GUI 进程。

仅启动 GUI（不带监督）也可以：

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
2. 程序开始常驻红外轮询；检测到人体靠近后打开摄像头。
3. 自动认证流程会依次显示：
   - `1. 红外检测`
   - `2. 人脸识别`
   - `3. 活体检测`
   - `4. 声纹识别（自然说话，无需固定口令）`
   - `5. 融合结果`
4. 活体检测时请眨眼一次，并左右转头。
5. 声纹识别时自然说一句中文即可，例如自我介绍或“请开门”。

也可以点击 `手动认证` 强制跑一遍完整流程。

## 4. 两阶段开门

`config.yaml` 中 `lock.flow` 决定开门方式：

- `agent_confirm`（默认）：硬件融合通过后只签发一个 300 秒有效、一次性认证凭证，**不立即开锁**。用户随后对语音 Agent 说“开门”，Agent 读取凭证并调用 `request_unlock`，由 Jetson 本地安全闸门验证后开锁。
- `immediate`：恢复原硬件流程，融合通过后由界面/控制器直接开锁（仍需 `--no-unlock` 或界面勾选保护）。

## 5. 注册人脸库

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

## 6. 注册声纹库

1. 在姓名输入框输入和人脸一致的姓名。
2. 点击 `采集声纹`。
3. 听到或看到提示后，自然说一句中文（不需要固定口令）。
4. 建议同一个人采集 3 次左右。

保存位置：

```text
database/voices/姓名/
```

声纹后端是 SpeechBrain ECAPA，文本无关：任意说话内容都可以用于识别。识别时只要求累计足够时长的清晰语音，并保证人脸身份与声纹身份一致。

## 7. 自动认证逻辑

默认开启自动认证：红外 + 人脸通过后自动开始完整认证。

```text
红外检测到靠近
  -> 打开摄像头并持续识别人脸
  -> 红外通过 + 人脸通过
  -> 自动开始活体检测
  -> 自动录制一段自然语音做声纹识别
  -> 检查人脸身份和声纹身份是否一致
  -> 多模态融合判定
  -> agent_confirm：写一次性凭证，等待语音 Agent 请求开锁
```

自动认证冷却时间在 `config.yaml` 的 `auto_auth.cooldown_seconds` 配置。

## 8. 语音 Agent 对话

### 8.1 Pipecat 连续对话原型

WSL 文件链路自测（不需要麦克风，输入/输出都是 WAV）：

```bash
python3 voice_agent_pipecat.py \
  --input-wav /tmp/input.wav \
  --output-wav /tmp/output.wav

RUN_PIPECAT_E2E=1 python3 test_voice_agent_pipecat.py
```

Jetson 一键启动（推荐）：

```bash
./run_smart_lock.sh
```

Windows 真机模式（Windows 请使用 conda 的 `python` 或完整路径，不要用 `python3`）：

```powershell
$py = "$env:USERPROFILE\miniconda3\envs\py3.10\python.exe"
& $py voice_agent_pipecat.py --list-devices
& $py voice_agent_pipecat.py --input-device <输入设备> --output-device <输出设备>
```

预启动模式（Jetson 监督脚本已自动使用）：先加载 FunASR，等待硬件认证凭证后再打开麦克风：

```bash
python3 voice_agent_pipecat.py --wait-auth
```

原型能力：Silero VAD 分段、用户说话时打断、FunASR 转写、FastGPT 流式回复、EdgeTTS 合成、sounddevice 输入输出。回声消除和长时间稳定性仍待现场持续验证。

### 8.2 单轮降级入口

先启动本地工具网关：

```bash
export LOCK_TOOL_TOKEN=replace-with-long-random-token
export LOCK_TOOL_GATEWAY_HOST=0.0.0.0
export LOCK_TOOL_GATEWAY_PORT=8787
export SMART_LOCK_NO_UNLOCK=1
python3 lock_tool_gateway.py --host "$LOCK_TOOL_GATEWAY_HOST" --port "$LOCK_TOOL_GATEWAY_PORT"
```

再运行语音 Agent：

```bash
python3 voice_agent.py --once
```

或绕过麦克风先测文本链路：

```bash
python3 voice_agent.py --text "你好，请开门" --require-fastgpt
```

安全规则：

- Agent 必须先读 `current_auth_context`，只有凭证 `available/fresh/authorized/unconsumed` 时才能调用 `request_unlock`。
- Agent 不能评分、否决或伪造硬件认证。
- 凭证被消费后立即失效；过期或人离开后失效。
- 没有有效凭证时，Agent 只能拒绝开门、引导重新认证或通知业主。

## 9. 语音引导

程序会优先播放 `audio_prompts/` 下的原始 PCM 文件：

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

## 10. 主要配置

配置文件：`config.yaml`

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

声纹和麦克风（已取消固定口令）：

```yaml
speaker:
  backend: speechbrain_ecapa
  min_score: 0.7
  voice_dir: "database/voices"
  input_device: null   # null = 使用 PulseAudio 默认录音源（Jetson 验证路径）
  sample_rate: 16000
  record_seconds: 2.5
  min_speech_seconds: 2.0
```

开门模式和安全凭证：

```yaml
lock:
  flow: agent_confirm   # immediate 为原自动开门流程

agent:
  safety:
    auth_context_max_age_seconds: 300.0
    auth_context_path: "logs/auth_context.json"
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

## 11. 单独测试硬件

测试红外传感器：

```bash
python3 test_sensor.py --count 10 --verbose
```

测试麦克风：

```bash
python3 test_audio.py --seconds 1
```

测试语音提示播放：

```bash
python3 test_voice_prompt.py voice_prompt
```

检查模型后端：

```bash
python3 check_runtime.py
```

回归无口令声纹接口：

```bash
python3 test_speaker_id.py
```

期望看到：

```text
face_backend=InsightFaceAuthenticator
liveness_backend=MediaPipeLivenessChecker
speaker_backend=SpeechBrainSpeakerAuthenticator
speaker_model=ready
```

## 12. 真实开锁

调试阶段保持：

```bash
./run_gui.sh
```

不要勾选 `允许真实开锁`，并保持网关环境变量 `SMART_LOCK_NO_UNLOCK=1`。

继电器、电磁锁、电源和现场安全确认无误后，再去掉 `--no-unlock`、设置 `SMART_LOCK_NO_UNLOCK=0`。真实开锁有物理安全风险，必须有人在门锁旁看护测试。
