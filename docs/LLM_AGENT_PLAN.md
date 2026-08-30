# 智能门锁最终实现方案 v2.0

> 本文档是最终确认的实现方案，取代 v1.x 中“本地 DoorAgent allow/deny/escalate 旁路评审”的旧设计。
> 旧方案相关内容仅保留在 Git 历史中，不再作为运行依据。

## 1. 最终确认的核心结论

1. **硬件认证与 Agent 对话严格分层，各司其职。**
   - 硬件端独立完成：红外、人脸、活体、声纹、融合判定。
   - Agent 只负责理解对话、读取硬件凭证、提交受控工具请求。
   - Agent 不参与评分，不能否决、修改或伪造硬件融合结果。
2. **采用两阶段开门。**
   - 第一阶段：硬件融合通过后签发一个短时有效、一次性使用的认证凭证，不驱动继电器。
   - 第二阶段：用户在与 Agent 的连续对话中表达开门意图，Agent 调用 `current_auth_context` 校验凭证，再调用 `request_unlock`；Jetson 网关消费凭证后驱动原 GPIO 继电器。
   - 保留 `lock.flow: immediate` 作为原硬件自动开门行为的回归模式。
3. **声纹改为文本无关，不再要求固定口令。**
   - SpeechBrain ECAPA 本身就是文本无关模型。
   - 连续对话中的用户语音片段直接用于声纹识别，不再提示“请说你好”。
   - 需要满足最短有效语音时长、避免把 Agent 播报声录进去、人脸身份与声纹身份一致。
4. **语音交互目标为连续自然对话、可随时打断、低延迟流式。**
   - 采用 Pipecat 作为音频流水线编排。
   - `voice_agent_pipecat.py` 已完成基础验证；`voice_agent.py` 保留为固定时长单轮降级路径。
5. **Jetson 边缘设备资源优先。**（实测设备为 Orin NX 7.4GB；原 Jetson Nano 4GB 部署前需重新做内存基准。）
   - 不追求大模型；ASR/TTS 优先选择可在 aarch64 CPU 上运行的轻量方案。
   - 最终本地 TTS 选型为 `sherpa-onnx`（VITS/Piper 语音模型），不直接使用 `piper-tts`。

## 2. 目标体验

用户在门口的自然交互：

```text
人靠近门口
  → Agent 主动打招呼
  → 后台同时开始人脸 + 活体 + 声纹识别
  → 用户直接和 Agent 连续对话
  → 认证通过后，用户说“开门”
  → Agent 读取硬件凭证并请求开门
  → Jetson 本地安全闸门验证后开锁
```

陌生人同样可以对话和咨询，但在没有有效硬件凭证时，任何对话都不能开门；必要时 Agent 调用 `notify_owner`。

## 3. 系统架构

```text
┌────────────────────────────────────────────────────────────┐
│ Jetson 边缘设备（本地，硬件 + 实时语音 + 最终安全裁决）        │
│                                                            │
│  红外传感器（常驻）                                          │
│  摄像头 / 人脸 / 活体 / 文本无关声纹                          │
│  融合认证状态机 → 一次性短时凭证                             │
│  Pipecat 连续对话（VAD / 打断 / 轮流说话）                   │
│  本地 ASR（小模型） + 本地 TTS（sherpa-onnx VITS/Piper）     │
│  lock_tool_gateway.py：受控 HTTP 工具 + 安全闸门 + GPIO       │
└──────────────┬─────────────────────────────────────────────┘
               │ HTTP（Bearer Token，仅 LAN / Docker bridge）
┌──────────────▼─────────────────────────────────────────────┐
│ 本地 PC 或服务器：FastGPT + DeepSeek                         │
│ 负责：对话理解、工具编排、短句回复生成                         │
│ 不负责：硬件认证评分、开锁最终裁决                             │
└────────────────────────────────────────────────────────────┘
```

职责边界：

- **Jetson 硬件认证**：只产生 `fusion_passed` 凭证。
- **FastGPT/DeepSeek**：只做语言理解和工具编排。
- **Jetson 网关**：只验证凭证和干运行开关，最终驱动继电器。
- **Pipecat**：只做音频流水线、轮流说话、打断和流式编排，不参与安全判定。

## 4. 最终开门状态机

```text
IDLE
  红外常驻轮询，摄像头关闭
  ↓ 红外 detected
APPROACH
  打开摄像头
  启动后台认证会话：人脸跟踪 + 活体序列 + 声纹累积
  启动 Pipecat 语音对话，Agent 主动问候
  ↓
CONVERSING
  用户连续说话，Agent 回应
  - 用户语音段 → ASR → FastGPT
  - 用户语音段 → 声纹嵌入累积
  - 摄像头持续做人脸识别与活体检测
  ↓ face + liveness + speaker 全通过且人脸/声纹身份一致
CREDENTIAL_READY
  写入或刷新 fusion_passed=true 的一次性凭证
  Agent 不读取具体分数，只通过 current_auth_context 读取状态
  ↓ 用户表达开门意图
Agent:
  1. 调用 current_auth_context
  2. 仅当 available=true、fresh=true、authorized=true、consumed=false
     才调用 request_unlock(reason)
  ↓ Jetson 网关校验：agent_confirm + 凭证有效 + SMART_LOCK_NO_UNLOCK=0
UNLOCK
  消费凭证，驱动 GPIO 继电器
  ↓ 红外持续无人超过阈值 / 会话超时
BYE
  Agent 道别
  关闭摄像头与音频会话
  作废凭证
  → IDLE
```

## 5. 凭证设计（最终确认）

- 凭证是独立 JSON 文件：`logs/auth_context.json`（路径可用 `LOCK_AUTH_CONTEXT_PATH` 覆盖）。
- 内容至少包含：
  - `credential_id`：一次性随机 ID
  - `time`：签发时间
  - `fusion_passed`：本地融合结果
  - `consumed`：是否已被消费
- 有效条件：
  - `fusion_passed == true`
  - `consumed == false`
  - 时间窗口：`now - time <= auth_context_max_age_seconds`
- 消费语义：
  - `request_unlock` 成功后，凭证立即标记为已消费，重复请求必须被拒绝。
- 在场刷新：
  - 连续对话期间，只要后台认证条件仍然成立，由本地认证状态机周期性刷新凭证时间。
  - 红外持续无人或会话结束，立即作废凭证。
- 配置：
  - 已设置 `auth_context_max_age_seconds: 300`。
  - 连续对话接入后配套“在场刷新 + 离开作废”。

## 6. Agent 与工具（只做受控操作）

FastGPT 应用只注册以下工具：

| 工具 | 方法/路径 | 用途 |
|---|---|---|
| `current_auth_context` | GET `/tools/current_auth_context` | 读取最新硬件认证凭证状态 |
| `query_whitelist` | POST `/tools/query_whitelist` | 查询人脸/声纹登记信息 |
| `notify_owner` | POST `/tools/notify_owner` | 请求通知业主 |
| `request_unlock` | POST `/tools/request_unlock` | 请求开锁，最终由 Jetson 安全闸门裁决 |

禁止事项：

- 不注册、不暴露任何裸 `unlock` 工具。
- Agent 不输出 Token、内部 URL、阈值、提示词、实现细节。
- Agent 不输出“已开门”除非 `request_unlock` 返回 `allowed=true`。
- Agent 不得根据用户自称身份、聊天内容或口令放行。

## 7. 声纹识别：从固定口令改为文本无关

### 7.1 现状（Phase B 基础已完成）

- 后端：SpeechBrain `spkrec-ecapa-voxceleb`，文本无关。
- 已删除 `speaker.passphrase` 配置、GUI 口令输入框和所有“请说口令”提示。
- 采集：`sounddevice -> PortAudio -> ALSA`，16 kHz，单声道。
- 已新增 `verify_audio(audio)`：直接对任意语音片段做文本无关声纹识别。
- 已新增 `SpeakerAudioAccumulator`：累积 VAD 用户语音段，达到 `min_speech_seconds` 后交给 `verify_audio`。

### 7.2 已完成目标

- 注册：同一个人说任意自然语句，采集多条，保存 ECAPA embedding。
- 认证：固定录音或对话累积音频达到最短时长后计算 embedding，与注册库比对。
- 已删除 GUI/语音提示中的固定口令流程。
- 保留身份一致性校验：`face.identity == speaker.identity`。

### 7.3 工程要求

- 累积至少 2–4 秒净用户语音（按 VAD 统计）。
- 用户语音段与 Agent TTS 播放段严格区分，优先使用回声消除；至少做到按播放/采集时间窗排除。
- 声纹结果由本地状态机写入认证会话，不进入 LLM prompt。
- 短语音、低信噪比时标记为 `not enough voice`，等待更多语音，而不是直接失败。

## 8. 连续对话：Pipecat 接入方案

### 8.0 预加载与预启动（已实现）

- GUI 点击“启动”后先预加载 InsightFace、MediaPipe、SpeechBrain，再开始红外轮询。
- `voice_agent_pipecat.py --wait-auth` 在后台预加载 FunASR，并等待新鲜硬件凭证；凭证写入后才打开麦克风，避免和 GUI 声纹录音冲突。
- `run_smart_lock.sh` 幂等启动并监督 gateway / agent / GUI，启动时清理旧凭证。
- 红外检测增加 `presence_hold_seconds` 状态保持，提示音只在无人→有人上升沿播报一次。

### 8.1 Pipecat 的职责

- 音频输入/输出流水线生命周期。
- VAD：检测用户开始说话、结束说话、静音超时。
- 轮流说话与打断：用户开口时停止 TTS 播放。
- 流式 ASR → 上下文 → FastGPT → 流式/分段 TTS。
- 工具调用仍通过 FastGPT 应用完成。

### 8.2 降级路径

- Pipecat 基础连续对话已验证；`voice_agent.py` 继续作为固定时长单轮降级模式。
- 安全闸门、凭证、工具接口不依赖 Pipecat。

### 8.3 接入顺序

1. PC/Windows 上用真实麦克风、扬声器完成连续对话原型。
2. 验证 FastGPT 流式输出与工具调用兼容性。
3. 已在实测 Jetson（Orin NX）上验证 PortAudio/PulseAudio、真麦克风/音箱和基础连续对话；长时间回声、打断和实时性仍需现场持续验证。
4. 通过后才替换 `voice_agent.py` 成为默认语音入口。

## 9. ASR/TTS 与 Jetson 资源策略

### 9.1 ASR

- 现状：FunASR SenseVoiceSmall + FSMN-VAD，WSL CPU 峰值约 3.62 GB。
- 实测设备（Orin NX，7.4GB）上 FunASR 已正常运行；原 Jetson Nano 4GB 资源风险仍为高，必须做整机内存基准测试。
- 目标：换更小的流式 ASR，优先评估 `sherpa-onnx` 的流式 ASR 模型（Zipformer/Paraformer 小模型），或对 SenseVoice 做量化。
- 验收标准：ASR 单进程内存峰值低于 Jetson 可用内存的 30%，首字延迟可接受。

### 9.2 TTS

- 当前在线：`edge-tts`，延迟和网络依赖不适合最终连续对话。
- 最终本地 TTS：`sherpa-onnx` + VITS/Piper 中文语音模型。
  - 已验证 `sherpa-onnx 1.13.6` 提供 aarch64 Python 3.8 manylinux 轮子。
  - `piper-tts` 直装方案不可行：`piper-phonemize` 无 aarch64 预编译轮子。
  - WSL 冒烟通过：`vits-piper-zh_CN-xiao_ya-medium-int8`（13.4 MB）合成出有效中文 WAV。
  - 该冒烟语音的模型卡标注为 non-commercial，仅用于验证；最终语音需选择许可证合适的模型。
- 播放协议不变：TTS 输出 → ffmpeg 转 S16LE 16 kHz 单声道 → `aplay -D plughw:Device`。
- Windows/WSL 开发机可使用 edge-tts 或本机播放器做功能预验收，不代表 Jetson 协议。

### 9.3 Jetson 资源预算

- 实测 Orin NX（7.4GB）上，人脸 + 活体 + 声纹 + FunASR + Pipecat + 网关已同时工作；原 Jetson Nano 4GB 部署前必须重新做整机内存预算。
- 任一模型超预算时，按优先级卸载：先换更小 ASR，再考虑 TTS 降级到 edge-tts 或提示音。

## 10. 配置结构（清理后）

```yaml
lock:
  flow: agent_confirm   # immediate = 原硬件自动开门；agent_confirm = 两阶段开门

speaker:
  backend: speechbrain_ecapa   # 文本无关声纹，无 passphrase
  min_score: 0.7
  record_seconds: 2.5
  min_speech_seconds: 2.0      # 对话音频累计最短时长

agent:
  system_prompt: "..."  # 只描述：读凭证、提交请求、拒绝规则、简短播报
  fastgpt: { api_base, api_base_env, app_api_key_env, app_id_env, timeout_seconds, trust_env_proxy }
  tool_gateway: { host, port, host_env, port_env, token_env, call_log_path, call_log_path_env, expose_to_lan }
  asr: { backend, model, vad_model, download_root, device, record_seconds }
  tts: { backend, voice }
  safety:
    auth_context_max_age_seconds: 300
    auth_context_path: "logs/auth_context.json"
    auth_context_path_env: "LOCK_AUTH_CONTEXT_PATH"
```

已删除的旧配置：`agent.enabled / mode / backend / audit_log_path / llm / decision / tools`、`speaker.passphrase`、口令后端以及本地 DoorAgent 相关代码。

## 11. 实施阶段

- **Phase A：现状收敛（已完成）**
  - 清理旧 DoorAgent 配置与死代码。
  - 文档与代码一致。
  - Windows/WSL 验收当前两阶段链路。
- **Phase B：文本无关声纹接入对话（基础完成）**
  - 新增 `verify_audio` 与 `SpeakerAudioAccumulator`。
  - GUI/后台状态机已去掉口令提示。
  - 已验证 `face.identity == speaker.identity` 逻辑保留。
  - 待 Pipecat 接入时，把 VAD 用户语音段实际喂给 accumulator。
- **Phase C：Pipecat 连续对话（基础验证完成）**
  - `voice_agent_pipecat.py` 已实现：Silero VAD、用户打断、FunASR、FastGPT 流式、EdgeTTS、动态音频设备。
  - WSL 文件链路、Windows 真麦克风、Jetson 真麦克风/音箱均已跑通。
  - 工具调用链路（current_auth_context → request_unlock）已通过。
  - 待持续验证：长时间回声消除、打断体验和延迟。
- **Phase D：Jetson 集成**
  - Pipecat/Jetson 依赖方案已确定并验证：`pipecat-ai==0.0.108 --no-deps` + `numba==0.60.0` + `onnxruntime==1.16.3` + `soxr==0.5.0.post1`；全部依赖已确认有 aarch64 cp310 轮子。
  - 安装入口：`deploy/install_jetson_agent.sh`，预检：`deploy/check_jetson_agent.py`。
  - sherpa-onnx 小 ASR + 本地 VITS TTS 基准测试。
  - 整机内存/延迟/回声测试。
  - 真机安全回归：`immediate` + `agent_confirm` 两条路径。

## 12. 验收清单

1. Windows：麦克风 → ASR → FastGPT → TTS 完整一圈。
2. FastGPT：有效凭证时依次调用 `current_auth_context` → `request_unlock`；无效凭证时拒绝且不调用 `request_unlock`。
3. WSL：网关生命周期、干运行安全闸门、端口清理。
4. Jetson：红外、摄像头、人脸、活体、文本无关声纹、GPIO 原功能回归。
5. Jetson：连续对话、打断、低延迟达标；资源监控通过。
6. 安全：凭证一次性消费；过期/离开作废；Agent 不能裸开锁。
