from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import requests

from smart_lock.config import AppConfig, VoiceFeedbackConfig, load_config


@dataclass(frozen=True)
class ChatReply:
    text: str
    raw: dict[str, Any] | None = None


class FastGPTChatClient:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._api_base = config.agent.fastgpt.api_base.rstrip("/")
        self._api_key = os.getenv(config.agent.fastgpt.app_api_key_env, "")
        self._app_id = os.getenv(config.agent.fastgpt.app_id_env, "")
        self._timeout = config.agent.fastgpt.timeout_seconds
        self._session = requests.Session()
        self._session.trust_env = config.agent.fastgpt.trust_env_proxy

    @property
    def configured(self) -> bool:
        return bool(self._api_base and self._api_key)

    def chat(self, text: str, chat_id: str) -> ChatReply:
        if not self.configured:
            raise RuntimeError(
                f"{self._config.agent.fastgpt.app_api_key_env} is not set; "
                "cannot call FastGPT"
            )

        payload: dict[str, Any] = {
            "chatId": chat_id,
            "stream": False,
            "detail": False,
            "messages": [
                {"role": "system", "content": self._config.agent.system_prompt},
                {"role": "user", "content": text},
            ],
        }
        if self._app_id:
            payload["appId"] = self._app_id

        response = self._session.post(
            self._chat_url(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        raw = response.json()
        return ChatReply(text=_extract_reply_text(raw), raw=raw)

    def chat_stream(self, text: str, chat_id: str):
        """Yield assistant text deltas from the FastGPT SSE stream."""
        if not self.configured:
            raise RuntimeError(
                f"{self._config.agent.fastgpt.app_api_key_env} is not set; "
                "cannot call FastGPT"
            )

        payload: dict[str, Any] = {
            "chatId": chat_id,
            "stream": True,
            "detail": False,
            "messages": [
                {"role": "system", "content": self._config.agent.system_prompt},
                {"role": "user", "content": text},
            ],
        }
        if self._app_id:
            payload["appId"] = self._app_id

        response = self._session.post(
            self._chat_url(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout,
            stream=True,
        )
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="ignore")
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data = raw_line[5:].strip()
            if not data or data == "[DONE]":
                continue
            delta = _extract_stream_delta(data)
            if delta:
                yield delta

    async def chat_stream_async(self, text: str, chat_id: str):
        """Async generator wrapper for :meth:`chat_stream`."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def produce() -> None:
            try:
                for delta in self.chat_stream(text, chat_id):
                    loop.call_soon_threadsafe(queue.put_nowait, delta)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, produce)
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    def _chat_url(self) -> str:
        if self._api_base.endswith("/api/v1"):
            return f"{self._api_base}/chat/completions"
        if self._api_base.endswith("/api"):
            return f"{self._api_base}/v1/chat/completions"
        return f"{self._api_base}/api/v1/chat/completions"


class FunASRTranscriber:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._model = None

    def preload(self) -> None:
        """Load the ASR model now instead of during the first user turn."""
        self._get_model()

    def transcribe(self, wav_path: Path) -> str:
        model = self._get_model()
        result = model.generate(input=str(wav_path), language="zh", use_itn=True)
        text = _extract_asr_text(result)
        try:
            from funasr.utils.postprocess_utils import rich_transcription_postprocess

            return rich_transcription_postprocess(text).strip()
        except (ImportError, ValueError):
            return text

    def _get_model(self):
        if self._model is not None:
            return self._model

        try:
            from funasr import AutoModel
        except Exception as exc:  # noqa: BLE001 - runtime dependency diagnostic.
            raise RuntimeError("FunASR is not installed; install requirements-agent.txt") from exc

        asr = self._config.agent.asr
        model = _local_model_or_id(asr.download_root, asr.model)
        vad_model = _local_model_or_id(asr.download_root, asr.vad_model)
        kwargs: dict[str, Any] = {
            "model": model,
            "device": asr.device,
            "disable_update": True,
        }
        if vad_model:
            kwargs["vad_model"] = vad_model
            kwargs["vad_kwargs"] = {"max_single_segment_time": 30000}
        self._model = AutoModel(**kwargs)
        return self._model


class EdgeTTSPlayer:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        if self._config.agent.tts.backend == "none":
            return
        if self._config.agent.tts.backend == "edge_tts":
            asyncio.run(self._speak_edge_tts(text))
            return
        if self._config.agent.tts.backend == "print":
            print(f"TTS: {text}")
            return
        raise ValueError(f"Unsupported TTS backend: {self._config.agent.tts.backend}")

    async def _speak_edge_tts(self, text: str) -> None:
        try:
            import edge_tts
        except Exception as exc:  # noqa: BLE001 - runtime dependency diagnostic.
            raise RuntimeError("edge-tts is not installed; install requirements-agent.txt") from exc

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            output = Path(tmp.name)
        try:
            proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
            communicate = edge_tts.Communicate(
                text,
                self._config.agent.tts.voice,
                proxy=proxy,
            )
            await communicate.save(str(output))
            _play_audio_file(output, self._config.voice_feedback)
        finally:
            output.unlink(missing_ok=True)


class VoiceAgent:
    def __init__(self, config: AppConfig, require_fastgpt: bool = False) -> None:
        self._config = config
        self._client = FastGPTChatClient(config)
        self._require_fastgpt = require_fastgpt

    def handle_text(self, text: str, chat_id: str = "smart-lock-voice") -> ChatReply:
        clean = text.strip()
        if not clean:
            return ChatReply("I did not hear anything.")
        if self._client.configured:
            return self._client.chat(clean, chat_id=chat_id)
        if self._require_fastgpt:
            return self._client.chat(clean, chat_id=chat_id)
        return ChatReply(f"Local fallback received: {clean}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart lock voice agent")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--text", help="Bypass microphone and send this text to the agent")
    parser.add_argument("--once", action="store_true", help="Record one microphone turn then exit")
    parser.add_argument("--loop", action="store_true", help="Run repeated microphone turns")
    parser.add_argument("--no-tts", action="store_true", help="Print response without audio playback")
    parser.add_argument("--require-fastgpt", action="store_true", help="Fail if FastGPT API key is missing")
    parser.add_argument("--chat-id", default="smart-lock-voice")
    parser.add_argument(
        "--device",
        default=None,
        type=_sounddevice_device,
        help="Override sounddevice input device by numeric index or name",
    )
    parser.add_argument("--seconds", type=float, default=None, help="Override recording seconds")
    parser.add_argument("--check-deps", action="store_true", help="Print voice dependency status and exit")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.check_deps:
        print(json.dumps(check_dependencies(), ensure_ascii=False, indent=2))
        return 0

    voice_agent = VoiceAgent(config, require_fastgpt=args.require_fastgpt)
    tts = EdgeTTSPlayer(config)

    if args.text is not None:
        reply = voice_agent.handle_text(args.text, chat_id=args.chat_id)
        print(reply.text)
        if not args.no_tts:
            tts.speak(reply.text)
        return 0

    if not args.once and not args.loop:
        parser.error("use --text, --once, or --loop")

    transcriber = FunASRTranscriber(config)
    while True:
        wav_path = record_wav(config, device=args.device, seconds=args.seconds)
        try:
            text = transcriber.transcribe(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
        print(f"USER: {text}")
        reply = voice_agent.handle_text(text, chat_id=args.chat_id)
        print(f"AGENT: {reply.text}")
        if not args.no_tts:
            tts.speak(reply.text)
        if args.once:
            break
    return 0


def check_dependencies() -> dict[str, str]:
    status: dict[str, str] = {}
    for module in ("pipecat", "funasr", "modelscope", "edge_tts", "requests"):
        try:
            imported = __import__(module)
            status[module] = str(getattr(imported, "__version__", "installed"))
        except Exception as exc:  # noqa: BLE001 - diagnostic command.
            status[module] = f"missing: {type(exc).__name__}: {exc}"
    try:
        import sounddevice as sd

        sd.query_devices()
        status["sounddevice"] = "installed"
    except Exception as exc:  # noqa: BLE001 - diagnostic command.
        status["sounddevice"] = f"unavailable: {type(exc).__name__}: {exc}"
    return status


def _local_model_or_id(download_root: str, model_id: str) -> str:
    if not model_id or not download_root:
        return model_id
    candidate = Path(download_root) / model_id.rstrip("/").rsplit("/", 1)[-1]
    if (candidate / "config.yaml").exists() or (candidate / "configuration.json").exists():
        return str(candidate)
    return model_id


def record_wav(config: AppConfig, device: str | None, seconds: float | None) -> Path:
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001 - runtime dependency diagnostic.
        raise RuntimeError(
            "sounddevice is unavailable. On Jetson install: "
            "sudo apt install portaudio19-dev libportaudio2"
        ) from exc

    sample_rate = config.speaker.sample_rate
    duration = seconds if seconds is not None else config.agent.asr.record_seconds
    frames = int(sample_rate * duration)
    input_device = device if device is not None else config.speaker.input_device
    print(f"recording seconds={duration} sample_rate={sample_rate} device={input_device}")
    audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32", device=input_device)
    sd.wait()
    mono = audio.reshape(-1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0:
        mono = mono / peak
    pcm = np.clip(mono * 32767.0, -32768, 32767).astype(np.int16)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return wav_path


def _extract_stream_delta(data: str) -> str:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if isinstance(content, str) and content:
            return content
    return ""


def _extract_reply_text(raw: dict[str, Any]) -> str:
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
    response_data = raw.get("responseData")
    if isinstance(response_data, list):
        texts = [str(item.get("text", "")).strip() for item in response_data if isinstance(item, dict)]
        text = "\n".join(item for item in texts if item)
        if text:
            return text
    if isinstance(raw.get("text"), str):
        return raw["text"].strip()
    return json.dumps(raw, ensure_ascii=False)


def _extract_asr_text(result: Any) -> str:
    if isinstance(result, list) and result:
        return _extract_asr_text(result[0])
    if isinstance(result, dict):
        for key in ("text", "sentence"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(result.get("raw_text"), str):
            return result["raw_text"].strip()
    if isinstance(result, str):
        return result.strip()
    return ""


def _sounddevice_device(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def _play_audio_file(path: Path, config: VoiceFeedbackConfig) -> None:
    if sys.platform == "win32":
        try:
            from edge_playback.win32_playback import play_mp3_win32

            play_mp3_win32(str(path))
            return
        except Exception:  # noqa: BLE001 - continue to external player fallbacks.
            pass

    if sys.platform.startswith("linux") and config.backend == "aplay_pcm":
        missing = [command for command in ("ffmpeg", "aplay") if not shutil.which(command)]
        if missing:
            raise RuntimeError(
                "Jetson audio playback requires the verified ALSA path; missing: "
                + ", ".join(missing)
            )
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as tmp:
            pcm_path = Path(tmp.name)
        try:
            converted = subprocess.run(
                [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-f",
                    "s16le",
                    "-ar",
                    str(config.pcm_rate),
                    "-ac",
                    str(config.pcm_channels),
                    str(pcm_path),
                ],
                check=False,
            )
            if converted.returncode != 0:
                raise RuntimeError("ffmpeg failed to convert EdgeTTS audio to ALSA PCM")
            played = subprocess.run(
                [
                    "aplay",
                    str(pcm_path),
                    "-r",
                    str(config.pcm_rate),
                    "-f",
                    config.pcm_format,
                    "-c",
                    str(config.pcm_channels),
                    "-D",
                    config.pcm_device,
                ],
                check=False,
            )
            if played.returncode != 0:
                raise RuntimeError(
                    f"ALSA aplay failed for configured device: {config.pcm_device}"
                )
            return
        finally:
            pcm_path.unlink(missing_ok=True)

    players = [
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
        ["mpg123", "-q", str(path)],
        ["mpv", "--no-video", "--really-quiet", str(path)],
    ]
    for command in players:
        if shutil.which(command[0]):
            subprocess.run(command, check=False)
            return
    print(f"TTS audio saved but no player found: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
