# 智能门锁 + 语音 Agent PPT 汇报稿

> 版本日期：2026-08-30。本文按当前代码与实测状态编写，可直接作为逐页 PPT 提纲。
> 技术方案见 `docs/LLM_AGENT_PLAN.md`，部署操作见 `docs/DEPLOYMENT.md`。

## 汇报口径

- **已验证**：已经在对应环境执行并得到结果。
- **已实现、待板端复测**：代码和本机测试完成，但最新版本还未重新同步到开发板。
- **规划**：尚未成为当前运行链路，不应放在“项目成果”中。
- 实际开发板检测为 Jetson Orin NX 级别设备，约 7.4 GiB 可用内存；“Jetson Nano 4GB”是原目标平台，仍需单独做资源压测。

---

## 第1页：封面

**标题：** 基于 Jetson 的多模态智能门锁与低延迟语音 Agent

**副标题：** 本地融合认证、一次性安全凭证、FastGPT 工具调用与异常事件闭环

**页脚：** 项目名称 / 汇报人 / 日期

**讲解重点：** 这不是让大模型直接控制门锁，而是在已验证的硬件认证流程上增加受控语音交互。

---

## 第2页：背景与目标

**现有痛点**

- 单一身份识别容易受到照片、冒用身份和环境噪声影响。
- 传统门锁缺少自然交互，异常情况也缺少统一记录和后续通知入口。
- 大模型适合理解语言，但不适合直接承担开锁安全判定。

**项目目标**

1. 在边缘端完成人脸、活体、声纹和红外融合认证。
2. 认证通过后，用户通过自然语音表达“开门”意图。
3. Agent 只能调用受控工具，最终开锁仍由 Jetson 本地安全闸门决定。
4. 认证异常自动写入事件文件，为后续 WorkBuddy/Bot 通知提供入口。

**本页结论：** 对话能力增强体验，硬件认证保持安全权威。

---

## 第3页：当前成果概览

| 能力 | 当前状态 |
|---|---|
| 红外、摄像头、人脸、活体、声纹 | 开发板原流程已跑通 |
| 多模态融合认证 | 已实现，阈值和权重可配置 |
| 两阶段开门 | 已实现并完成工具调用测试 |
| Pipecat 连续语音 Agent | Windows/WSL/开发板基础链路已验证 |
| FastGPT + DeepSeek | 本地 FastGPT 运行，真实工具调用测试通过 |
| 一次性认证凭证 | 已实现，有效期、消费状态和过期检查完整 |
| 异常事件服务 | 本机生命周期和开发板GUI自动触发均已验证 |
| WorkBuddy/Bot 通知 | 提示词和同步脚本已准备，自动任务尚未正式启用 |
| 真实门锁继电器 | 当前用串口风扇模拟，正式继电器待受控测试 |

**建议视觉：** 使用四个数字强调：`4种本地检测 / 300秒凭证 / 2个Agent工具 / 4个受监督服务`。

---

## 第4页：总体部署架构

**主图：** `docs/architecture.png`，需要编辑时使用 `docs/architecture.svg`。

**Jetson 边缘端负责**

- 硬件采集、多模态识别和融合判定。
- 本地 ASR、TTS、Pipecat 音频流水线。
- 一次性凭证、工具网关、异常服务和最终执行器。

**Windows/WSL 负责**

- FastGPT Agent 工作流。
- DeepSeek API 模型调用。
- SSH 隧道、部署管理，以及后续 WorkBuddy 通知任务。

**边界：** DeepSeek 不接触摄像头特征、声纹 embedding 或真实执行器，只看到对话和受控工具结果。

---

## 第5页：硬件多模态认证

```text
红外触发
  -> InsightFace 人脸识别
  -> MediaPipe 眨眼和转头活体
  -> SpeechBrain ECAPA 文本无关声纹
  -> 人脸/声纹身份一致性
  -> 本地加权融合
```

**当前融合参数**

| 模块 | 权重 |
|---|---:|
| 人脸 | 0.45 |
| 活体 | 0.25 |
| 声纹 | 0.20 |
| 红外 | 0.10 |

- 融合阈值：`0.78`。
- 声纹阈值：`0.70`，最短有效语音 `2秒`。
- 默认 `require_all=false`，按加权总分判断；参数全部位于 `config.yaml`。

**本页结论：** Agent 不参与这些分数的计算，也不能修改融合结果。

---

## 第6页：两阶段受控开门

**主图：** `docs/unlock_sequence.png` 或 `docs/unlock_sequence.svg`。

**第一阶段：硬件认证**

- 融合通过后不立即开锁。
- Jetson 写入一个 `credential_id`，包含签发时间、`fusion_passed` 和 `consumed`。
- 当前有效期为 `300秒`，只能消费一次。

**第二阶段：语音确认**

1. 用户说“开门”。
2. FastGPT 必须先调用 `current_auth_context`。
3. 仅凭证可用、新鲜、已授权、未消费时才调用 `request_unlock(reason)`。
4. Jetson 本地网关再次检查凭证和 `SMART_LOCK_NO_UNLOCK` 后执行动作。

**兼容原流程：** `lock.flow=immediate` 可恢复原硬件融合通过后直接执行的模式。

---

## 第7页：低延迟语音 Agent

```text
麦克风
 -> sounddevice / PortAudio
 -> Pipecat + Silero VAD
 -> SenseVoice / FunASR 本地识别
 -> FastGPT + DeepSeek
 -> 本地 sherpa-onnx TTS 优先
 -> ALSA aplay 播放
```

**Pipecat 的作用**

- 管理持续麦克风输入和一轮语音的开始/结束。
- 处理 VAD、轮流说话、打断和流水线生命周期。
- 串联 ASR、Agent 回复和 TTS，但不参与安全判定。

**Jetson 音频协议**

- 输入：16 kHz、单声道，`sounddevice/PortAudio`。
- 输出：16 kHz、单声道、S16_LE，ALSA `aplay`。
- GUI 提示音和 Agent 回复复用已经验证过的硬件播放路径。

**准确口径：** ASR/TTS 在本地，DeepSeek 仍需要网络，因此当前不是“完全离线对话”。端到端低延迟数据仍需板端重新测量。

---

## 第8页：异常事件闭环

**自动触发条件**

1. 红外持续检测到人员，但超过 `3秒`仍无法识别人脸。
2. 完整认证进入融合阶段后失败，例如活体、声纹或身份一致性未通过。

**事件处理**

```text
认证失败
 -> localhost:8790/event
 -> 原子写入 latest_event.json
 -> SCP 同步到 Windows
 -> WorkBuddy 定时读取
 -> Bot 通知
 -> 成功后标记 processed=true
```

- 同一次人员停留只写一次，离开超过 `5秒`后重新布防。
- 事件服务异常只记日志并重试，不会改变硬件认证或开锁结果。
- 当前为最小实现，只保留最新一条事件，不是完整消息队列。

**当前状态：** 服务、文件写入、processed标记和生命周期已在本机验证；开发板GUI通过mock红外、真实摄像头和真实人脸模型完成自动触发验证。

---

## 第9页：安全设计

**四层约束**

1. **融合层：** 只有 Jetson 本地 `FusionEngine` 能产生认证结果。
2. **凭证层：** 短时、一次性、过期和重复消费均拒绝。
3. **Agent层：** 只注册 `current_auth_context` 和 `request_unlock`，不暴露裸 `unlock()`。
4. **执行层：** Bearer Token、`SMART_LOCK_NO_UNLOCK` 和本地执行器共同控制真实动作。

**已测试的两种情况**

| 场景 | 实际工具调用 | 结果 |
|---|---|---|
| 有效凭证 + “请开门” | `current_auth_context -> request_unlock` | 干运行阻止真实动作 |
| 无效凭证 + “请开门” | 仅 `current_auth_context` | 不调用开锁工具 |

**本页结论：** 即使模型误解或提示词失效，本地安全闸门仍能拒绝无凭证请求。

---

## 第10页：部署、端口与进程治理

**端口分工**

| 服务 | 默认端口 | 部署位置 | 配置入口 |
|---|---:|---|---|
| FastGPT | 3300 | Windows/WSL | `FASTGPT_PORT` / `FASTGPT_API_BASE` |
| 门锁工具网关 | 8787 | Jetson | `LOCK_TOOL_GATEWAY_PORT` |
| 异常事件服务 | 8790 | Jetson本机 | `LOCK_EVENT_SERVICE_PORT/URL` |

**一键管理**

```bash
./run_smart_lock.sh start
./run_smart_lock.sh status
./run_smart_lock.sh restart
./run_smart_lock.sh stop
```

- 监督 GUI、Pipecat Agent、工具网关、异常服务。
- 子进程异常退出后自动拉起。
- 停止时清理 PID、临时凭证和 `.tmp` 文件，并释放端口。
- Windows 侧通过 `deploy/run_jetson_full_stack.ps1` 管理 SSH 双向隧道和远程服务。

---

## 第11页：测试与验证结果

| 测试项 | 结果 |
|---|---|
| 原硬件正向认证流程 | 开发板已跑通 |
| FastGPT Web | `localhost:3300` 返回 200 |
| Agent 有效/无效凭证工具调用 | 两个真实场景均通过 |
| 工具网关生命周期 | start/stop/restart/crash/recovery 通过 |
| 异常服务生命周期 | start/stop/restart/crash/recovery 通过 |
| 端口与父进程环境清理 | 通过 |
| Pipecat 单元链路 | 通过 |
| Windows/WSL 文件语音链路 | 通过 |
| 开发板真麦克风/音箱基础对话 | 历史版本已通过 |
| 最新异常自动触发 | 开发板GUI完整链路通过 |
| 最新Pipecat `aplay`输出改动 | 已上传，待单独语音复测 |

**资源观察**

- 实测整机运行 GUI、语音模型和服务时约使用 `5.5 GiB`，剩余约 `1.6 GiB`。
- SenseVoice 进程和 GUI 是主要内存占用者。
- 因此当前结果适用于 Orin NX 级设备，不能直接等同于 Nano 4GB 已满足要求。

---

## 第12页：关键工程问题与解决方案

| 问题 | 解决方案 |
|---|---|
| 红外持续为 true 导致重复播报 | 上升沿触发 + 状态保持 + 离开重置 |
| 模型首次使用延迟高 | GUI和FunASR启动时预加载 |
| GUI声纹和Agent同时抢麦克风 | Agent先等待硬件凭证，认证结束后再开麦 |
| Agent可能绕过认证 | 一次性凭证 + 本地工具网关，不暴露裸开锁 |
| FastGPT无法直接访问Jetson | SSH本地/反向隧道连接3300和8787 |
| 服务异常退出污染端口 | PID监督、信号清理、强制中断恢复测试 |
| 异常情况没有记录 | 增加独立事件服务和单文件原子写入 |
| Jetson音频路径不统一 | 输入统一PortAudio，输出统一ALSA `aplay` |

---

## 第13页：当前边界与风险

1. 最新Pipecat `aplay`输出代码已上传，但还需要单独完成真实语音播放复测。
2. 当前执行器是串口风扇模拟开锁，真实继电器需有人看护并保持可断电状态测试。
3. DeepSeek API仍依赖网络，网络不可用时硬件认证可运行，但 `agent_confirm` 对话开门不可用。
4. 回声消除、长时间打断稳定性和端到端延迟还缺少正式数据。
5. `latest_event.json` 只保存最新事件，多个异常事件可能覆盖；生产版本应改为SQLite或消息队列。
6. WorkBuddy提示词和同步脚本已完成，但Bot渠道和正式定时任务尚未接入。
7. Nano 4GB资源余量未知，需要更小ASR或模型量化方案。

**汇报原则：** 这些是下一阶段工程工作，不影响已完成的架构验证结论。

---

## 第14页：下一步计划

**短期：完成板端验收**

1. 用真实红外和现场陌生人再做一次非mock事件演示。
2. 验证 Pipecat 通过 `aplay` 的真实扬声器输出。
3. 复测完整 `start/restart/stop/crash` 和端口清理。

**中期：形成可演示闭环**

1. 接入 WorkBuddy 定时任务和一个 Bot 通知渠道。
2. 用真实异常事件完成“记录 -> 同步 -> 通知 -> processed”演示。
3. 记录 ASR、模型调用、TTS和总响应延迟。

**长期：产品化**

- 事件存储升级为SQLite队列。
- 加入回声消除和更小的流式ASR。
- 在真实继电器和Nano 4GB上完成资源、安全、断网和断电测试。

**结束语：** 项目已经证明“硬件认证负责安全、Agent负责交互、网关负责执行”的组合可行。

---

## 现场演示脚本

建议控制在4分钟内，真实动作保持 `SMART_LOCK_NO_UNLOCK=1`。

1. 展示FastGPT页面和Jetson GUI均已启动。
2. 正常人员完成红外、人脸、活体和声纹认证。
3. 对门锁说“请开门”，展示FastGPT依次调用两个工具，最终被干运行保护阻止。
4. 注入无效凭证或清除凭证，再说“请开门”，展示只调用查询工具。
5. 模拟陌生人/融合失败，展示 `latest_event.json` 的事件内容。
6. 执行processed命令，展示状态变为 `true`。

演示失败时的降级方案：使用已经准备好的日志、JSON文件和两张流程图说明结果，不在现场临时修改安全开关。

## 答辩问题准备

**Q：为什么不让DeepSeek直接判断是否开门？**

A：语言模型输出存在不确定性。项目只让它理解用户意图，开门必须依赖Jetson本地签发的一次性硬件凭证。

**Q：Pipecat是不是Agent？**

A：不是。Pipecat是实时语音流水线，负责VAD、轮流说话、打断和串联ASR/LLM/TTS；FastGPT + DeepSeek才承担对话理解和工具编排。

**Q：断网后还能用吗？**

A：硬件识别、融合认证和`immediate`模式仍可本地运行；当前DeepSeek对话需要网络，因此`agent_confirm`模式会受影响。

**Q：Agent会不会阻止已经通过的硬件认证？**

A：不会修改或否决融合结果。当前产品流程有意要求用户再说开门，Agent只转交请求，最终由Jetson读取原凭证。

**Q：是否已经部署到Jetson Nano？**

A：已验证设备实际是Orin NX级别。Nano 4GB是目标兼容平台，但必须先完成内存和延迟压测，不能直接宣称已经支持。

**Q：异常事件为什么只用一个JSON？**

A：这是快速验证WorkBuddy自动任务的最小实现。它具备原子写入和processed状态，产品化时会升级为SQLite队列。

## PPT素材索引

- 总体架构图：`docs/architecture.png` / `docs/architecture.svg`
- 两阶段流程图：`docs/unlock_sequence.png` / `docs/unlock_sequence.svg`
- 架构图源文件：`docs/architecture.dot`
- 流程图源文件：`docs/unlock_sequence.dot`
- FastGPT Agent配置：`deploy/fastgpt_agent_config.json`
- WorkBuddy任务提示词：`deploy/WORKBUDDY_EVENT_PROMPT.md`
- 部署和验证记录：`docs/DEPLOYMENT.md`

建议补拍的现场素材：设备全景、GUI五步认证截图、FastGPT工具调用截图、`latest_event.json`截图、风扇模拟开锁照片。
