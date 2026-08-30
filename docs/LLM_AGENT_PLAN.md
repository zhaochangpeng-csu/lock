# 智能门锁最终实现方案 v2.1

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

## 2. 当前 MVP 体验与后续目标

当前已经实现并作为默认运行依据的流程：

```text
人靠近门口
  → 先完成人脸 + 活体 + 声纹融合认证
  → 认证通过并签发一次性凭证
  → Pipecat 打开麦克风，用户说“开门”
  → Agent 读取硬件凭证并请求开门
  → Jetson 本地安全闸门验证后开锁
```

这种顺序避免 GUI 声纹认证与 Agent 抢占同一个麦克风。未来可评估“对话音频同时用于声纹累积”，但它不是当前成果。陌生人或认证失败会写入异常事件；当前 Agent 在没有有效凭证时不能开门。

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
│  lock_event_service.py：异常事件 → latest_event.json          │
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
- **异常服务**：只记录认证失败，故障不能影响融合判定、凭证或开锁。

## 4. 当前开门状态机

```text
IDLE
  红外常驻轮询，摄像头关闭
  ↓ 红外 detected
APPROACH
  打开摄像头
  依次执行人脸、活体、声纹认证
  ↓
AUTHENTICATING
  本地融合判定
  - 失败：旁路写入异常事件，不改变原硬件判定
  - 通过：签发一次性凭证
  ↓
CREDENTIAL_READY
  Pipecat 打开麦克风并提示用户说开门指令
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
RESET
  人员离开后关闭摄像头与音频会话
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
- 当前生命周期：
  - 每次融合通过签发一个新凭证；失败重试不会覆盖仍有效且未消费的通过凭证。
  - 人员离开、会话重置或监督脚本重启时删除凭证。
  - 周期性在场刷新不是当前实现，若未来需要超过 300 秒的长会话再增加。
- 配置：
  - 已设置 `auth_context_max_age_seconds: 300`。
  - 已实现离开作废和启动清理。

## 6. Agent 与工具（只做受控操作）

当前 FastGPT 智能门锁应用只注册以下两个安全必需工具：

| 工具 | 方法/路径 | 用途 |
|---|---|---|
| `current_auth_context` | GET `/tools/current_auth_context` | 读取最新硬件认证凭证状态 |
| `request_unlock` | POST `/tools/request_unlock` | 请求开锁，最终由 Jetson 安全闸门裁决 |

网关代码仍保留 `query_whitelist` 和未接通知后端的 `notify_owner` 原型，但它们不属于当前 FastGPT 发布配置。通知链路后续由 WorkBuddy/Bot 任务承接。

禁止事项：

- 不注册、不暴露任何裸 `unlock` 工具。
- Agent 不输出 Token、内部 URL、阈值、提示词、实现细节。
- Agent 不输出“已开门”除非 `request_unlock` 返回 `allowed=true`。
- Agent 不得根据用户自称身份、聊天内容或口令放行。

## 7. 声纹识别：从固定口令改为文本无关

### 7.1 现状（Phase B 基础已完成）

- 后端：SpeechBrain `spkrec-ecapa-voxceleb`，文本无关。
- 已删除 `speaker.passphrase` 配置、GUI 口令输入框和所有“请说口令”提示。
- 采集：`sounddevice -> PortAudio -> PulseAudio 默认输入源`，16 kHz，单声道。
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
- `run_smart_lock.sh` 幂等启动并监督 gateway / event service / agent / GUI，启动时清理旧凭证和临时文件。
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

- 当前 Pipecat 已优先使用本地 `sherpa-onnx` + VITS/Piper 中文语音模型，失败后再降级到 `edge-tts`，最后尝试 espeak。
  - 已验证 `sherpa-onnx 1.13.6` 提供 aarch64 Python 3.8 manylinux 轮子。
  - `piper-tts` 直装方案不可行：`piper-phonemize` 无 aarch64 预编译轮子。
  - WSL 冒烟通过：`vits-piper-zh_CN-xiao_ya-medium-int8`（13.4 MB）合成出有效中文 WAV。
  - 该冒烟语音的模型卡标注为 non-commercial，仅用于验证；最终语音需选择许可证合适的模型。
- 播放协议不变：TTS 输出 → ffmpeg 转 S16LE 16 kHz 单声道 → `aplay -D plughw:Device`。
- Windows 开发机使用 sounddevice 输出；Jetson 固定使用上述 `aplay` 协议。

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

event_reporting:
  enabled: true
  service_url: "http://127.0.0.1:8790"
  unknown_face_delay_seconds: 3.0
```

已删除的旧配置：`agent.enabled / mode / backend / audit_log_path / llm / decision / tools`、`speaker.passphrase`、口令后端以及本地 DoorAgent 相关代码。

## 11. 异常事件最小闭环

- GUI 中红外持续有人且 3 秒无法识别人脸时，自动上报一次 `abnormal_behavior`。
- 完整融合认证失败时也会上报；同一次人员停留只保留一次成功上报，人员离开后重新布防。
- 无界面 `SmartLockController` 在摄像头异常或融合失败时同样上报。
- `lock_event_service.py` 在 `127.0.0.1:8790` 提供记录、读取和 processed 接口，并用临时文件替换保证原子写入。
- 当前 `latest_event.json` 只保留最新一条事件，是快速验证方案，不等同于生产事件队列。
- WorkBuddy 的同步脚本和任务提示词已经写好；Bot渠道和正式定时任务后续接入。
- 事件上报是非权威旁路：服务宕机、超时或通知失败都不能改变硬件融合结果。

## 12. 实施阶段

- **Phase A：现状收敛（已完成）**
  - 清理旧 DoorAgent 配置与死代码。
  - 文档与代码一致。
  - Windows/WSL 验收当前两阶段链路。
- **Phase B：文本无关声纹接入对话（基础完成）**
  - 新增 `verify_audio` 与 `SpeakerAudioAccumulator`。
  - GUI/后台状态机已去掉口令提示。
  - 已验证 `face.identity == speaker.identity` 逻辑保留。
  - 当前默认仍由 GUI 单独录制声纹；把 Pipecat VAD 语音段喂给 accumulator 属于后续优化。
- **Phase C：Pipecat 连续对话（基础验证完成）**
  - `voice_agent_pipecat.py` 已实现：Silero VAD、用户打断、FunASR、FastGPT 流式、本地 sherpa-onnx 优先 TTS。
  - WSL 文件链路、Windows 真麦克风、Jetson 真麦克风/音箱均已跑通。
  - 工具调用链路（current_auth_context → request_unlock）已通过。
  - Jetson 输出已改为统一 ALSA `aplay` 协议，最新改动待板端重新同步验证。
  - 待持续验证：长时间回声消除、打断体验和端到端延迟。
- **Phase D：Jetson 集成**
  - Pipecat/Jetson 依赖方案已确定并验证：`pipecat-ai==0.0.108 --no-deps` + `numba==0.60.0` + `onnxruntime==1.16.3` + `soxr==0.5.0.post1`；全部依赖已确认有 aarch64 cp310 轮子。
  - 安装入口：`deploy/install_jetson_agent.sh`，预检：`deploy/check_jetson_agent.py`。
  - sherpa-onnx 小 ASR + 本地 VITS TTS 基准测试。
  - 整机内存/延迟/回声测试。
  - 真机安全回归：`immediate` + `agent_confirm` 两条路径。
- **Phase E：异常事件（本机实现完成，板端待复测）**
  - 认证失败自动上报、单事件文件、processed 标记均已实现。
  - 事件服务 start/restart/stop/crash/recovery 和端口清理已通过。
  - 待 SSH 恢复后同步开发板，并接入 WorkBuddy/Bot 定时通知。

## 13. 验收清单

1. Windows：麦克风 → ASR → FastGPT → TTS 完整一圈。
2. FastGPT：有效凭证时依次调用 `current_auth_context` → `request_unlock`；无效凭证时拒绝且不调用 `request_unlock`。
3. WSL：网关生命周期、干运行安全闸门、端口清理。
4. Jetson：红外、摄像头、人脸、活体、文本无关声纹、GPIO 原功能回归。
5. Jetson：连续对话、打断、低延迟达标；资源监控通过。
6. 安全：凭证一次性消费；过期/离开作废；Agent 不能裸开锁。
7. 异常：陌生人超时和融合失败能写入事件；服务故障不影响认证；通知成功后才能标记 processed。
