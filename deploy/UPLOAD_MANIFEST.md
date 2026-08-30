# Smart Lock Deployment Manifest

## Jetson Nano

Upload and run the hardware project on Jetson:

```text
config.yaml
requirements.txt
requirements-agent.txt
run_gui.sh
run_smart_lock.sh
main.py
gui.py
check_runtime.py
test_agent.py
test_audio.py
test_sensor.py
test_voice_prompt.py
deploy/test_lifecycle.py
deploy/test_fastgpt_agent.py
deploy/download_voice_models.py
deploy/install_jetson_agent.sh
deploy/requirements-agent-jetson.txt
deploy/check_jetson_agent.py
download_models.py
lock_tool_gateway.py
voice_agent.py
voice_agent_pipecat.py
smart_lock/
audio_prompts/README.md
```

Runtime data is not copied from the PC. Download/build it directly on Jetson:

```text
database/
models/
audio_prompts/*.pcm
logs/
.env
```

Jetson responsibilities:

- Read infrared sensor, camera, microphone, and GPIO relay.
- Run face recognition, liveness detection, speaker recognition.
- Run `voice_agent.py` fallback and `voice_agent_pipecat.py --wait-auth` with local SenseVoice ASR.
- Use online `edge-tts` today; local sherpa-onnx VITS is selected but not wired yet.
- Expose only authenticated HTTP tools for FastGPT.
- Enforce `request_unlock` safety gates locally.

Start order on Jetson:

```bash
export LOCK_TOOL_TOKEN=replace-with-long-random-token
export LOCK_TOOL_GATEWAY_HOST=0.0.0.0
export LOCK_TOOL_GATEWAY_PORT=8787
export SMART_LOCK_NO_UNLOCK=1
python3 lock_tool_gateway.py --host "$LOCK_TOOL_GATEWAY_HOST" --port "$LOCK_TOOL_GATEWAY_PORT"
DISPLAY=:0 ./run_gui.sh
run_smart_lock.sh
python3 deploy/download_voice_models.py
deploy/install_jetson_agent.sh
deploy/requirements-agent-jetson.txt
deploy/check_jetson_agent.py
```

Lifecycle smoke test on Jetson or WSL:

```bash
python3 deploy/test_lifecycle.py gateway --host 127.0.0.1 --port 9877
```

## Local PC Or Server

Deploy or configure these outside Jetson:

```text
FastGPT Docker deployment
DeepSeek API key/model provider
FastGPT smart-lock agent app
FastGPT HTTP tools pointing to Jetson
```

The local PC/server does not need the hardware model/data directories.

FastGPT tool setup is documented in `deploy/FASTGPT_TOOLS.md`.
FastGPT lifecycle preflight is documented in `deploy/test_lifecycle.py`.

## Deferred

These integrations are intentionally left for a later phase:

```text
Workbuddy IM
QQ notification
WeChat notification
```
