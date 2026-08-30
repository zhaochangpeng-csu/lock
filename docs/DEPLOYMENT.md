# Smart Lock Agent Deployment

> 最终实现方案以 `docs/LLM_AGENT_PLAN.md`（v2.0）为准。本文件记录部署步骤与验证状态。

## Current Verification Status

Final plan confirmed:

- Hardware fusion issues a one-time, short-lived credential; the voice Agent only reads and consumes it. The Agent cannot rescore or override the hardware result.
- Target voice interaction is continuous natural conversation with barge-in, built on Pipecat. `voice_agent_pipecat.py` is the working prototype; `voice_agent.py` remains the fixed-duration fallback.
- Speaker authentication is text-independent SpeechBrain ECAPA. The fixed passphrase prompt has been removed; `verify_audio` and `SpeakerAudioAccumulator` now support dialogue-audio verification.

Validated in the current WSL environment (2026-08-30):

- FastGPT is reachable on `http://localhost:3300` and a real `voice_agent.py --text` call returned a normal Agent reply, so the FastGPT chat path is usable.
- FastGPT streaming is usable: `/api/v1/chat/completions` with `stream: true` returns `text/event-stream` SSE chunks.
- FastGPT tool-call chain passed with a fresh dry-run credential: `current_auth_context` was called before `request_unlock`, and the dry-run safety gate returned `allowed=false`.
- `deploy/configure_fastgpt_agent.py` idempotently creates/updates and publishes the `智能门锁管家` Agent and its HTTP toolset.
- A real FastGPT conversation called `notify_owner`; an unlock request called `current_auth_context` and did not call `request_unlock` when authentication was stale.
- The Jetson tool gateway start, restart, forced-stop recovery, authentication, dry-run gate, and port cleanup tests pass in WSL.
- The text path `voice_agent.py -> FastGPT -> DeepSeek -> Jetson HTTP tool` is working.
- SenseVoiceSmall and FSMN-VAD were downloaded under `models/funasr`, loaded successfully on CPU, and transcribed the bundled Chinese sample. The WSL process peaked at about 3.62 GB RAM, so this model must pass a Jetson whole-system memory test before it is accepted.

Local TTS decision (2026-08-30):

- `piper-tts` direct installation is NOT viable for Jetson aarch64: `piper-phonemize` has no aarch64 wheel in the available index.
- `sherpa-onnx 1.13.6` has a Python 3.8 aarch64 manylinux wheel and supports offline VITS/Piper voices. It is the chosen local TTS runtime.
- WSL smoke test passed: `vits-piper-zh_CN-xiao_ya-medium-int8` (13.4 MB) synthesized a valid 2.72 s Chinese WAV with non-zero audio. The model card says that voice is non-commercial, so it is a smoke-test voice only; select the final voice after checking its license.
- Pipecat now prefers local sherpa-onnx VITS and falls back to EdgeTTS, then espeak. The final voice still needs a license review and a physical-board latency benchmark.

Validated Pipecat prototype in WSL and Windows (2026-08-30):

- WSL file pipeline: 16 kHz WAV input -> Silero VAD -> FunASR -> FastGPT streaming -> EdgeTTS -> WAV output passed.
- Windows file pipeline: same path passed using the `imageio-ffmpeg` bundled ffmpeg fallback; output WAV was generated.
- Windows `--list-devices` works and enumerates 25 audio devices. Use the conda Python (`python` or the full path), not the Microsoft Store `python3` stub.
- `voice_agent_pipecat.py` now resolves ffmpeg as: system PATH `ffmpeg` first, then `imageio-ffmpeg`.

Jetson Pipecat dependency resolution (2026-08-30, validated):

- Install with `deploy/install_jetson_agent.sh`: `pipecat-ai==0.0.108 --no-deps` plus aarch64-compatible `numba==0.60.0`, `onnxruntime==1.16.3`, `soxr==0.5.0.post1`, `numpy<2`.
- The substituted combination passed the WSL full file-pipeline E2E before deployment.

Validated on the physical Jetson (2026-08-30):

- `deploy/check_jetson_agent.py` passed.
- Unit tests passed: `test_agent`, `test_speaker_id`, `test_voice_agent`, `test_voice_agent_pipecat`.
- `check_runtime.py` passed: InsightFace / MediaPipe / SpeechBrain ready, face/voice database loaded.
- Infrared sensor on `/dev/ttyUSB0`, XFM-DP microphone, and USB audio output were detected.
- Pipecat real-time voice conversation worked with real microphone and speaker.
- Full chain with simulated hardware credential worked: Jetson Pipecat ASR -> FastGPT -> `current_auth_context` -> `request_unlock` -> dry-run gate `allowed=false`.

Still to validate on the physical Jetson:

- Real GPIO relay action (requires supervised hardware test; keep `SMART_LOCK_NO_UNLOCK=1`).
- Long-running barge-in / echo / latency stability.
- Direct PC firewall rule for `http://192.168.1.111:3300` (E2E used an SSH reverse tunnel while the direct path was blocked).

`lock.flow` selects the unlock workflow. `immediate` preserves the original hardware behavior. `agent_confirm` records a short-lived hardware fusion credential and waits for the user to ask the voice Agent to open the door. The Agent cannot rescore or override that credential.

## Windows Voice Check

Use the native Windows Python environment so the test can access the Windows microphone and speaker:

```powershell
cd C:\Users\hoyo\Desktop\lock
$py = "$env:USERPROFILE\miniconda3\envs\py3.10\python.exe"

& $py voice_agent.py --check-deps
& $py -c "import sounddevice as sd; print(sd.default.device); print(sd.query_devices())"
& $py test_audio.py --device 1 --seconds 2
& $py voice_agent.py --text "你好，你是谁？" --require-fastgpt
& $py voice_agent.py --once --device 1 --require-fastgpt
```

Replace device `1` with an input-capable index reported by `sounddevice`. On this Windows machine, input device `1` recorded successfully at 16 kHz. The dependency check, local SenseVoice inference, FastGPT response, EdgeTTS generation, and Windows playback have been validated.

On Jetson, the audio protocol is:

- Input: `sounddevice -> PortAudio -> PulseAudio default source`, XFM-DP microphone, mono 16 kHz.
- Pipecat TTS: local sherpa-onnx first, then EdgeTTS/espeak fallback -> 16 kHz mono S16LE -> ALSA `aplay` using `voice_feedback.pcm_device`.
- GUI prompts: original `aplay` PCM path with `voice_feedback.pcm_device`, rate, format, and channels remains unchanged.
- `voice_agent.py` remains the fixed-duration fallback entry point.

## Target Split

Use a Jetson edge device for hardware and voice I/O. The validated board is an Orin NX (7.4GB RAM); the deployment plan remains compatible with Jetson Nano after a memory re-benchmark. Use a local PC, lab server, or cloud VM for FastGPT.

Keep Docker on the FastGPT side. Keep the Jetson realtime path native Python plus systemd/venv/conda, because microphone capture, ASR/TTS latency, GPIO, and relay control are easier to supervise directly on the hardware.

```text
Jetson edge device (validated on Orin NX):
  smart lock Python project
  camera / infrared / microphone / relay
  face / liveness / speaker recognition
  Pipecat continuous voice agent + voice_agent.py fallback
  FunASR/SenseVoice today; smaller sherpa-onnx streaming ASR is the next step
  edge-tts TTS today; local sherpa-onnx VITS/Piper TTS is the next step
  authenticated lock tool gateway

Local PC or server:
  FastGPT
  DeepSeek API provider
  visual Agent workflow
```

## Jetson Environment

Install the base project dependencies first:

```bash
# Pipecat requires Python >= 3.10. Use Miniforge/conda Python 3.10 on Jetson.
python -m pip install -r requirements.txt
python -m pip install -r requirements-agent.txt
```

On Jetson aarch64, install Pipecat with the verified dependency set instead of the x86 constraints:

```bash
bash deploy/install_jetson_agent.sh
python deploy/check_jetson_agent.py
```

The Jetson installer uses `pipecat-ai==0.0.108 --no-deps` plus these aarch64-compatible substitutions: `numba==0.60.0`, `onnxruntime==1.16.3`, `soxr==0.5.0.post1`. This combination passed the WSL file-pipeline E2E.

Jetson audio system packages:

```bash
sudo apt-get update
sudo apt-get install -y portaudio19-dev libportaudio2 ffmpeg
```

Create Jetson runtime environment variables from the template:

```bash
cp deploy/jetson.env.example .env
```

Fill in:

```text
FASTGPT_API_BASE
FASTGPT_APP_API_KEY
FASTGPT_APP_ID
LOCK_TOOL_GATEWAY_HOST
LOCK_TOOL_GATEWAY_PORT
LOCK_TOOL_TOKEN
```

Keep `SMART_LOCK_NO_UNLOCK=1` while validating.

Quick local validation:

```bash
python3 test_agent.py
python3 test_voice_agent.py
python3 deploy/test_fastgpt_agent.py
python3 -m compileall main.py gui.py lock_tool_gateway.py voice_agent.py smart_lock test_agent.py test_voice_agent.py deploy
```

Hardware validation:

```bash
python3 test_sensor.py --count 10 --verbose
python3 test_audio.py --seconds 1
python3 test_voice_prompt.py voice_prompt
DISPLAY=:0 ./run_smart_lock.sh
```

`run_smart_lock.sh` is idempotent and supervised:
- starts/restarts `lock_tool_gateway.py`;
- starts/restarts `lock_event_service.py` on the configurable local event port;
- starts `voice_agent_pipecat.py --wait-auth`, which preloads FunASR and waits for a fresh hardware credential before opening the microphone;
- starts `gui.py --hardware --no-unlock`; the GUI preloads face/liveness/speaker models when `启动` is clicked;
- clears stale `auth_context.json` on startup.

An authentication failure in either `gui.py` or `SmartLockController` reports one `abnormal_behavior` event to the local event service. The report is a non-authoritative side effect: failure to report cannot stop or change hardware authentication. `latest_event.json` intentionally stores only the newest event for the first WorkBuddy integration.

Start the Jetson tool gateway:

```bash
export LOCK_TOOL_TOKEN=replace-with-long-random-token
export LOCK_TOOL_GATEWAY_HOST=0.0.0.0
export LOCK_TOOL_GATEWAY_PORT=8787
export SMART_LOCK_NO_UNLOCK=1
python3 lock_tool_gateway.py --host "$LOCK_TOOL_GATEWAY_HOST" --port "$LOCK_TOOL_GATEWAY_PORT"
```

Validate the gateway from the Jetson itself:

```bash
curl http://127.0.0.1:8787/health
curl -H "Authorization: Bearer ${LOCK_TOOL_TOKEN}" \
  http://127.0.0.1:8787/tools/current_auth_context
```

Run the lifecycle smoke test to verify start, restart, stop, auth, and port cleanup:

```bash
python3 deploy/test_lifecycle.py gateway --host 127.0.0.1 --port 9877
```

Current WSL validation result:

```text
start ok: http://127.0.0.1:9877/health
stop ok: port released
restart ok: service came back
crash stop ok: port released
post-crash restart ok: service came back
final stop ok: port released
env ok: parent process unchanged
```

Preload the local ASR model on Jetson:

```bash
python3 deploy/download_voice_models.py
```

Text-only voice Agent smoke test:

```bash
FASTGPT_API_BASE=http://<fastgpt-host>:3300 \
FASTGPT_APP_API_KEY=<fastgpt-app-api-key> \
FASTGPT_APP_ID=<fastgpt-app-id> \
python3 voice_agent.py --text "你好，测试门锁语音助手" --no-tts --require-fastgpt
```

Real FastGPT Agent tool-call test, after running the automated FastGPT configuration:

```bash
FASTGPT_API_BASE=http://<fastgpt-host>:3300 \
FASTGPT_APP_API_KEY=<fastgpt-app-api-key> \
FASTGPT_APP_ID=<fastgpt-app-id> \
LOCK_TOOL_TOKEN=<same-token-configured-in-fastgpt-tools> \
python3 deploy/test_fastgpt_agent.py
```

This test fails if FastGPT replies without calling a Jetson tool.

One-turn microphone test on Jetson:

```bash
FASTGPT_API_BASE=http://<fastgpt-host>:3300 \
FASTGPT_APP_API_KEY=<fastgpt-app-api-key> \
FASTGPT_APP_ID=<fastgpt-app-id> \
python3 voice_agent.py --once --no-tts
```

## FastGPT Environment

Deploy FastGPT on a PC/server with Docker Compose. Follow the current official FastGPT Docker documentation for the compose files and version-specific environment variables:

```text
https://doc.fastgpt.io/en/self-host/deploy/docker
```

FastGPT official deployment flow:

```text
1. Get configuration files
2. Modify environment variables
3. Open required ports
4. Start containers
5. Access FastGPT
6. Configure models
```

Configure DeepSeek in FastGPT:

```text
API base: https://api.deepseek.com
Model: deepseek-v4-flash
API key: DEEPSEEK_API_KEY
```

Recommended first model: `deepseek-v4-flash`. Switch to `deepseek-v4-pro` only when stronger reasoning is needed.

Create or update the FastGPT Agent, prompt, HTTP toolset, API key, and runtime `.env` from versioned configuration:

```bash
FASTGPT_ADMIN_PASSWORD=<fastgpt-root-password> \
python deploy/configure_fastgpt_agent.py \
  --tool-base-url http://172.18.0.1:8787
```

Use `http://<jetson-lan-ip>:8787` after moving the gateway to Jetson. The Agent prompt and tool schemas are stored in `deploy/fastgpt_agent_config.json`; they are not hardcoded only in the FastGPT UI.

Current local deployment attempt:

```text
FastGPT directory: /mnt/c/users/hoyo/desktop/fastgpt-smart-lock
FastGPT URL: http://localhost:3300
Compose ports: default 3000, 3003, 3006, 9000, 9001; local .env currently maps to 3300, 3303, 3316, 3390, 3391
Compose region: global
Status: FastGPT Web is running and reachable at http://localhost:3300. The official mongo:5.0.32 image pulled in this local Docker environment had 0-byte MongoDB binaries, so MongoDB was switched to bitnamilegacy/mongodb:6.0.
Local note: port 3000 is already occupied by another process, and port 3306 is also occupied. Use alternate ports such as 3300, 3303, 3316, 3390, and 3391 unless those services are stopped.
```

The generated compose file in `/mnt/c/users/hoyo/desktop/fastgpt-smart-lock/docker-compose.yml` has been adjusted to read these local `.env` variables:

```text
FASTGPT_HTTP_PORT
FASTGPT_MCP_PORT
FASTGPT_SANDBOX_PORT
FASTGPT_MINIO_PORT
FASTGPT_MINIO_CONSOLE_PORT
FASTGPT_FE_DOMAIN
FASTGPT_MCP_ENDPOINT
FASTGPT_SANDBOX_PROXY_URL
FASTGPT_SANDBOX_PREVIEW_PROXY_URL
```

Start or resume FastGPT:

```bash
cd /mnt/c/users/hoyo/desktop/fastgpt-smart-lock
cp /mnt/c/users/hoyo/desktop/lock/deploy/local-fastgpt.env.example .env
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY docker compose --profile prepull pull
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY docker compose up -d
curl -I http://localhost:3300
```

If Docker still resolves registry hosts to `198.18.x.x` and times out, fix Docker Desktop or system proxy first, then rerun the same commands.

Current local MongoDB note:

```text
Compose service fastgpt-mongo uses bitnamilegacy/mongodb:6.0.
Replica set name remains rs0.
FastGPT MONGODB_URI remains unchanged.
```

Current local validation:

```bash
curl -I http://localhost:3300
docker exec fastgpt-app node -e "fetch('http://172.18.0.1:8787/health').then(async r=>console.log(r.status, await r.text()))"
```

`172.18.0.1` is the Docker bridge gateway address for this WSL FastGPT app network. If FastGPT is moved to another machine, set the tool URL to `http://<jetson-ip>:8787` instead.

FastGPT lifecycle preflight, for a clean stopped deployment:

```bash
/home/hoyo/miniconda3/bin/conda run -n jax-gpu-py310 \
  python deploy/test_lifecycle.py fastgpt \
  --compose-dir /mnt/c/users/hoyo/desktop/fastgpt-smart-lock
```

Current manual lifecycle result:

```text
compose ok
stop ok: 3300, 3316, 3390, 3391, and 3303 released
start ok: http://localhost:3300 returned HTTP 200
restart ok: http://localhost:3300 returned HTTP 200 after docker compose restart
gateway cleanup ok: 8787 released after stopping the temporary gateway
manual kill note: docker kill fastgpt-app left the app stopped; docker compose up -d fastgpt-app restored it and http://localhost:3300 returned HTTP 200 again
```

Use `docker compose restart` or `docker compose up -d` for managed recovery. Do not use `docker kill` as an operational restart command.

After FastGPT is stopped and ports are free, run the full lifecycle test:

```bash
/home/hoyo/miniconda3/bin/conda run -n jax-gpu-py310 \
  python deploy/test_lifecycle.py fastgpt \
  --compose-dir /mnt/c/users/hoyo/desktop/fastgpt-smart-lock \
  --start
```

## FastGPT Tools

Register these HTTP tools in the FastGPT smart-lock app:

```text
GET  {JETSON_TOOL_BASE}/tools/current_auth_context
POST {JETSON_TOOL_BASE}/tools/query_whitelist
POST {JETSON_TOOL_BASE}/tools/notify_owner
POST {JETSON_TOOL_BASE}/tools/request_unlock
```

Every request must include:

```http
Authorization: Bearer ${LOCK_TOOL_TOKEN}
```

Do not register a raw `unlock` tool. FastGPT and DeepSeek may only request `request_unlock(reason)`.

The Python voice client prompt is configurable at `agent.system_prompt` in `config.yaml`. The authoritative FastGPT Agent prompt and tool workflow are in `deploy/fastgpt_agent_config.json` and are published by `deploy/configure_fastgpt_agent.py`; manual UI configuration is only a fallback.

Initial tool schemas:

```json
{
  "query_whitelist": {"person": "string"},
  "notify_owner": {"message": "string"},
  "request_unlock": {"reason": "string"}
}
```

See `deploy/FASTGPT_TOOLS.md` for the complete FastGPT tool setup and prompt guardrail.

## Upload Boundary

See `deploy/UPLOAD_MANIFEST.md` for the exact Jetson upload list and the local-only FastGPT responsibilities.

## WSL Validation

On the current development machine, the usable conda environment is `jax-gpu-py310`:

```bash
/home/hoyo/miniconda3/bin/conda run -n jax-gpu-py310 python --version
```

Run the local checks before copying files to Jetson:

```bash
/home/hoyo/miniconda3/bin/conda run -n jax-gpu-py310 python test_agent.py
/home/hoyo/miniconda3/bin/conda run -n jax-gpu-py310 python -m compileall main.py gui.py lock_tool_gateway.py test_agent.py smart_lock
```

Start and test the gateway in WSL:

```bash
LOCK_TOOL_TOKEN=test-token SMART_LOCK_NO_UNLOCK=1 \
  /home/hoyo/miniconda3/bin/conda run -n jax-gpu-py310 python lock_tool_gateway.py

curl http://127.0.0.1:8787/health
curl -H "Authorization: Bearer test-token" \
  http://127.0.0.1:8787/tools/current_auth_context
curl -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"reason":"测试请求开锁"}' \
  http://127.0.0.1:8787/tools/request_unlock
```

Lifecycle smoke test:

```bash
/home/hoyo/miniconda3/bin/conda run -n jax-gpu-py310 \
  python deploy/test_lifecycle.py gateway --host 127.0.0.1 --port 9877
```
