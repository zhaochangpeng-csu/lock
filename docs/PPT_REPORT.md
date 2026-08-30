# 智能门锁项目最终汇报材料

> 本文档用于 PPT 汇报，内容与最终代码一致。技术细节见 `docs/LLM_AGENT_PLAN.md`，部署步骤见 `docs/DEPLOYMENT.md`。

## 1. 一句话总结

在 Jetson 边缘设备上实现“红外触发 → 多模态身份认证 → 一次性凭证 → 连续语音 Agent → 受控开门”的智能门锁系统，硬件认证与 AI 对话严格分层，Agent 不能绕过本地安全闸门。

## 2. 项目背景与目标

- 传统门锁只有单一认证方式，缺少自然交互与可解释的安全边界。
- 目标：人在门口自然靠近，设备自动完成人脸、活体、声纹认证；用户直接和语音 Agent 对话，说“开门”即完成受控开锁。
- 关键约束：大模型 Agent 可以聊天，但不能评分、不能否决、不能伪造硬件认证结果。

## 3. 最终架构

```text
Jetson 边缘设备（实测 Orin NX，7.4GB 内存）
  红外传感器（常驻）
  摄像头 + InsightFace 人脸识别
  MediaPipe 活体检测
  SpeechBrain 文本无关声纹
  Pipecat 连续语音对话（VAD/打断/轮流说话）
  FunASR 本地语音识别
  EdgeTTS 在线语音合成（本地 sherpa-onnx 为下一步）
  lock_tool_gateway.py 本地安全闸门
  GPIO 继电器（干运行保护）

PC / 服务器
  FastGPT + DeepSeek
  只负责对话理解与工具编排，不参与安全评分
```

## 4. 核心业务流程

```text
系统启动
  ├─ GUI 预加载人脸/活体/声纹模型
  ├─ Agent 进程预加载 FunASR，等待硬件凭证
  └─ 监督脚本保证 gateway/agent/gui 常活

红外无人→有人（状态保持 3 秒，只播报一次）
  → 打开摄像头
  → 人脸识别
  → 活体检测（眨眼+转头）
  → 声纹识别（自然说话，无固定口令）
  → 身份一致性校验
  → 融合判定通过
  → 写一次性凭证（300 秒有效）

Agent 被凭证唤醒
  → 播报“认证通过，请说开门指令”
  → 用户说“请开门”
  → FastGPT 调用 current_auth_context
  → 凭证有效才调用 request_unlock
  → Jetson 网关消费凭证
  → SMART_LOCK_NO_UNLOCK=0 时才驱动继电器
```

## 5. 关键设计原则

1. **硬件认证与 Agent 对话分层**：
   - 硬件端只产生 `fusion_passed` 凭证；
   - Agent 只读取凭证、提交请求；
   - 网关只验证凭证和干运行开关。
2. **一次性凭证**：
   - `credential_id` + 时间戳 + `consumed`；
   - 过期、已消费、无效都拒绝；
   - 启动时清理旧凭证。
3. **文本无关声纹**：
   - 注册和认证都不要求固定口令；
   - 新增 `verify_audio` 和 `SpeakerAudioAccumulator`，为对话音频声纹做准备。
4. **模型预加载 / Agent 预启动**：
   - GUI 点击启动即加载三个识别模型；
   - `voice_agent_pipecat.py --wait-auth` 先加载 FunASR，再等凭证开麦。
5. **鲁棒启动**：
   - `run_smart_lock.sh` 幂等启动并监督 gateway/agent/gui；
   - 动态使用 PulseAudio 默认源/默认输出，不写死设备编号。

## 6. 已实现功能清单

| 模块 | 实现 |
|---|---|
| 红外 | 串口轮询，上升沿触发，状态保持，去重播报 |
| 人脸 | InsightFace ArcFace embedding，注册与识别 |
| 活体 | MediaPipe FaceLandmarker，眨眼 + 转头 |
| 声纹 | SpeechBrain ECAPA，文本无关 |
| 融合 | 加权融合：face 0.45 / liveness 0.25 / speaker 0.20 / sensor 0.10 |
| 凭证 | JSON 一次性凭证，300 秒 TTL |
| Agent | FastGPT + DeepSeek，流式对话 |
| 语音 | Pipecat + Silero VAD + FunASR + EdgeTTS |
| 工具 | current_auth_context / query_whitelist / notify_owner / request_unlock |
| 安全 | Bearer Token、凭证消费、`SMART_LOCK_NO_UNLOCK` 干运行 |
| GUI | PySide6，模型预加载，注册采集，状态显示 |
| 部署 | `run_smart_lock.sh` 监督启动 |

## 7. 实测结果

### 7.1 硬件环境

- 实测板子：Jetson Orin NX 系列，aarch64，Ubuntu 22.04，6 核，7.4GiB 内存，NVMe 233GB。
- 音频：XFM-DP 麦克风阵列 + USB C-Media 音箱。
- 传感器：`/dev/ttyUSB0` 红外，实测 `detected=True value=1`。
- 麦克风实测：16kHz 录音 peak=0.065，RMS=0.016。

### 7.2 模型与性能

| 项目 | 结果 |
|---|---|
| 人脸/活体/声纹后端 | `check_runtime.py` 全部 ready |
| FunASR 中文识别 | 正确识别，首次 RTF 约 1.27 |
| Pipecat 首句延迟 | 预加载前约 21 秒；预加载后开麦即用 |
| WSL 文件 E2E | 通过 |
| Windows 文件/真机 E2E | 通过 |
| Jetson 真机语音对话 | 通过 |
| 工具调用链 | `current_auth_context → request_unlock` 顺序正确，干运行 `allowed=false` |

### 7.3 验证矩阵

| 环境 | 硬件认证 | 语音 Agent | 工具调用 |
|---|---|---|---|
| WSL | dry-run 模拟 | 文件链路 | 通过 |
| Windows | mock 凭证 | 真麦克风 + 文件链路 | 通过 |
| Jetson | 真人脸/活体/声纹通过 | 真麦克风/音箱 | 通过（干运行） |

## 8. 过程中解决的关键问题

| 问题 | 根因 | 解决方案 |
|---|---|---|
| 红外提示疯狂重复 | 状态持续 true 时每 6 秒播报 | 上升沿播报 + 状态保持 |
| 首次认证极慢 | 模型全部懒加载 | GUI 启动预加载三个模型 |
| Agent 启动慢 | FunASR 第一句话才加载 | `--wait-auth` 预加载 |
| GUI 与 Agent 抢麦克风 | 同一 XFM 设备 | 凭证写入后 Agent 才开麦 |
| 声卡 underrun / 失真 | 16k→44.1k 二次重采样 | TTS 直接 ffmpeg 输出 44.1kHz |
| 设备编号漂移 | 写死 ALSA 设备号 | PulseAudio 默认源/默认 sink |
| Jetson 装不上 Pipecat | onnxruntime/numba/soxr 无新版 aarch64 wheel | `--no-deps` + 验证过的替代版本 |
| Pipecat 导入卡死 | NLTK 在线下载 punkt_tab | 预置离线 NLTK 数据 |
| FastGPT 直连不通 | PC 防火墙 | SSH 反向隧道完成 E2E，生产需开防火墙 |

## 9. 安全机制

- 调试默认 `--no-unlock`，网关默认 `SMART_LOCK_NO_UNLOCK=1`。
- Agent 只注册受控工具，不注册裸 `unlock`。
- `request_unlock` 必须满足：
  - `lock.flow == agent_confirm`
  - 凭证 available / fresh / authorized / unconsumed
  - `SMART_LOCK_NO_UNLOCK=0`
- 凭证一次性消费，重复请求拒绝。
- FastGPT 提示词禁止泄露 Token、URL、阈值和内部实现。

## 10. 部署方式

```bash
# Jetson
cd ~/smart_lock_ai_20260829_1915
./run_smart_lock.sh
```

监督脚本自动：
- 启动并拉起 gateway；
- 启动 `voice_agent_pipecat.py --wait-auth`；
- 启动 GUI；
- 清理旧凭证。

## 11. 遗留事项 / 下一步

1. 真实继电器开锁需在有人看护下测试，当前保持干运行。
2. FastGPT 直连需开放 PC 防火墙 `3300` 端口，并把 FastGPT 工具地址改为 `http://<jetson-ip>:8787`。
3. 长时间回声消除、打断、延迟稳定性需持续现场验证。
4. 本地离线 TTS：sherpa-onnx + VITS 中文模型已选型，待接入。
5. 最终形态：声纹直接取 Pipecat 对话音频，GUI 不再单独录音。
6. 原 Jetson Nano 4GB 部署时需重新做整机内存基准，必要时换 sherpa-onnx 小 ASR。

## 12. PPT 建议结构

1. 项目背景与痛点
2. 总体架构图
3. 硬件认证流水线
4. 语音 Agent 与工具调用
5. 安全凭证设计
6. 三个环境的验证结果
7. 关键问题与解决方案（选 3-5 个）
8. 实测数据
9. 部署与运维
10. 下一步规划
