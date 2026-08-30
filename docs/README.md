# 智能门锁项目文档索引

## 推荐阅读顺序

1. `PPT_REPORT.md`：逐页汇报稿、演示脚本、答辩问题和素材索引。
2. `LLM_AGENT_PLAN.md`：当前架构、职责边界、状态机和后续阶段。
3. `DEPLOYMENT.md`：Windows/WSL、FastGPT和Jetson部署及验证记录。
4. `../README.md`：项目使用、注册、认证和日常启动说明。
5. `../deploy/FASTGPT_TOOLS.md`：FastGPT Agent提示词和两个受控工具。
6. `../deploy/WORKBUDDY_EVENT_PROMPT.md`：异常事件定时任务提示词。

## PPT图形素材

| 素材 | PNG | 可编辑版本 | 源文件 |
|---|---|---|---|
| 总体部署架构 | `architecture.png` | `architecture.svg` | `architecture.dot` |
| 两阶段开门流程 | `unlock_sequence.png` | `unlock_sequence.svg` | `unlock_sequence.dot` |

修改 `.dot` 后重新生成：

```bash
dot -Tsvg docs/architecture.dot -o docs/architecture.svg
dot -Tpng -Gdpi=180 docs/architecture.dot -o docs/architecture.png
dot -Tsvg docs/unlock_sequence.dot -o docs/unlock_sequence.svg
dot -Tpng -Gdpi=180 docs/unlock_sequence.dot -o docs/unlock_sequence.png
```

## 当前状态口径

- 原硬件正向认证、基础Pipecat语音对话和开发板硬件链路已经验证。
- FastGPT真实工具调用、网关/事件服务生命周期和本机端口清理已经验证。
- 最新异常自动触发和Jetson `aplay`输出改动已实现，但因开发板SSH当前拒绝密钥交换，尚未同步到板端复测。
- WorkBuddy同步脚本和提示词已经完成；正式定时任务、Bot渠道和真实继电器属于后续工作。
- 实测板为Orin NX级别、约7.4 GiB内存；Nano 4GB只是目标兼容平台，不能宣称已经完成部署。

## 内容维护规则

- 不把目标架构写成已完成功能。
- 不把风扇串口模拟动作写成真实继电器开锁。
- 不宣称完全离线：当前DeepSeek API需要网络。
- 不宣称Agent决定身份或开锁；本地融合认证和Jetson安全闸门始终是权威。
- FastGPT当前发布配置只有`current_auth_context`和`request_unlock`两个工具。
