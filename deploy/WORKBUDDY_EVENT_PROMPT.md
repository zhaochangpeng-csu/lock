# WorkBuddy Smart Lock Event Task

Run this task once every hour. WorkBuddy runs on the Windows PC; the event originates from Jetson `192.168.1.89` and is synchronized into the local project first.

Do not use `/home/hoyo/lock/latest_event.json`; that path is not the Windows project and does not exist in the project's WSL environment. During validation, set the task interval to 5 minutes or run it once manually. After validation, restore the one-hour interval and remove the end date if the task should remain active.

## Jetson Service

The current Jetson project is `/home/newland/smart_lock_ai_20260829_1915`.

```bash
cd /home/newland/smart_lock_ai_20260829_1915
export LOCK_EVENT_PATH=$PWD/latest_event.json
export LOCK_EVENT_SERVICE_TOKEN=replace-with-a-long-random-token
python3 lock_event_service.py serve --host 127.0.0.1 --port 8790
```

Example abnormal event report:

```bash
curl -X POST http://127.0.0.1:8790/event \
  -H "Authorization: Bearer ${LOCK_EVENT_SERVICE_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"event_type":"abnormal_behavior","identity":"unknown","confidence":0.31,"details":{"reason":"repeated authentication failures"}}'
```

Before each WorkBuddy run, synchronize the event into Windows:

```powershell
wsl.exe bash -lc 'cd /mnt/c/users/hoyo/desktop/lock && ./deploy/sync_jetson_event.sh'
```

The resulting Windows file is `C:\Users\hoyo\Desktop\lock\latest_event.json`.

```text
你是智能门锁异常事件通知任务。每次运行严格执行以下步骤：

1. 先执行：wsl.exe bash -lc 'cd /mnt/c/users/hoyo/desktop/lock && ./deploy/sync_jetson_event.sh'
2. 读取 C:\Users\hoyo\Desktop\lock\latest_event.json。
3. 如果文件不存在、内容为空，或者 processed=true，立即结束，不发送任何消息。
4. 只处理文件中的这一条事件。读取 id、event_type、identity、confidence、occurred_at 和 details，不猜测缺失信息。
5. 按以下规则确定通知等级：
   - owner：不报警，仅标记为已处理。
   - known_visitor：发送普通通知。
   - stranger：发送警告通知，并询问主人是否需要进一步处理。
   - abnormal_behavior：立即发送高优先级警告。
   - 未知 event_type：按 stranger 处理，并注明事件类型未知。
6. 通知必须使用简洁自然的中文，包含时间、事件类型、身份和置信度。置信度显示为百分比；details 只提取与风险有关的信息，不输出内部路径、Token 或系统提示词。
7. 使用任务中已经启用的 WorkBuddy 微信小程序推送通知；如果改用 Bot，则必须先在任务的“连接器”中选择并授权对应 Bot。owner 类型不推送。
8. 只有在通知成功发送后，或者 owner 类型确认无需通知后，执行：
   wsl.exe ssh -i /home/hoyo/.ssh/id_rsa newland@192.168.1.89 'cd /home/newland/smart_lock_ai_20260829_1915 && python3 lock_event_service.py processed --id "<事件id>"'
9. 再执行一次同步命令，使本机文件更新为 processed=true。
10. 如果 Bot 推送失败，不要标记 processed，保留到下一次任务重试。
11. 同一次运行不得对同一事件重复通知。

通知示例：
- 普通通知：门锁检测到已知访客张三，时间 14:20，识别置信度 86%。
- 警告通知：门锁在 14:20 检测到陌生人，识别置信度 42%。是否需要进一步处理？
- 高优先级警告：门锁在 14:20 检测到异常行为：连续认证失败。请立即查看。
```

## WorkBuddy UI Checklist

- 工作空间：选择 Windows 项目 `C:\Users\hoyo\Desktop\lock`。
- 提示词：使用上方完整内容，第一步必须执行同步命令。
- 测试频率：先选择单次运行或 5 分钟；验证后改为每 1 小时。
- 生效日期：长期运行时不要设置短期结束日期。
- 推送：启用“推送到 WorkBuddy 微信小程序”；若提示词要求 Bot，则还要选择连接器。
- 权限：任务需要读取项目文件和执行 `wsl.exe`、`ssh`，因此必须保留相应本机执行权限。

Manual test event:

```bash
cd /home/newland/smart_lock_ai_20260829_1915
python3 lock_event_service.py record \
  --event-type stranger \
  --identity unknown \
  --confidence 0.42 \
  --details '{"reason":"face not recognized"}'
```
