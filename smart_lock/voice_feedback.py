from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from .config import VoiceFeedbackConfig

LOGGER = logging.getLogger(__name__)


class VoiceFeedback:
    def __init__(self, config: VoiceFeedbackConfig) -> None:
        self._config = config
        self._last_spoken: dict[str, float] = {}
        self._sherpa_tts = None

    def speak(
        self,
        key: str,
        text: str | None = None,
        force: bool = False,
        block: bool | None = None,
    ) -> None:
        if not self._config.enabled:
            return
        now = time.monotonic()
        if not force and now - self._last_spoken.get(key, 0.0) < self._config.cooldown_seconds:
            return
        self._last_spoken[key] = now

        message = text or self._config.messages.get(key, key)
        if self._try_play_pcm(key, block):
            return
        if self._try_sherpa_tts(message, block):
            return
        if self._config.fallback_to_tts_command and self._try_tts(message, block):
            return
        LOGGER.info("Voice prompt skipped: key=%s text=%s", key, message)

    def _try_play_pcm(self, key: str, block: bool | None) -> bool:
        if self._config.backend != "aplay_pcm":
            return False
        pcm_path = Path(self._config.pcm_dir) / f"{key}.pcm"
        if not pcm_path.exists() or shutil.which("aplay") is None:
            return False

        command = [
            "aplay",
            str(pcm_path),
            "-r",
            str(self._config.pcm_rate),
            "-f",
            self._config.pcm_format,
            "-c",
            str(self._config.pcm_channels),
            "-D",
            self._config.pcm_device,
        ]
        return self._run(command, block)

    def _get_sherpa_tts(self):
        if self._sherpa_tts is not None:
            return self._sherpa_tts
        import sherpa_onnx

        base = Path(self._config.sherpa_model_dir)
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(base / "zh_CN-xiao_ya-medium.onnx"),
            tokens=str(base / "tokens.txt"),
            lexicon=str(base / "lexicon.txt"),
            data_dir=str(base),
        )
        model_cfg = sherpa_onnx.OfflineTtsModelConfig(
            vits=vits,
            num_threads=self._config.sherpa_num_threads,
            provider="cpu",
        )
        tts_cfg = sherpa_onnx.OfflineTtsConfig(
            model=model_cfg,
            rule_fsts=f"{base}/date.fst,{base}/number.fst,{base}/phone.fst",
        )
        self._sherpa_tts = sherpa_onnx.OfflineTts(tts_cfg)
        return self._sherpa_tts

    def _try_sherpa_tts(self, text: str, block: bool | None) -> bool:
        try:
            tts = self._get_sherpa_tts()
            audio = tts.generate(text, sid=0, speed=self._config.sherpa_speed)
            samples = np.asarray(audio.samples, dtype=np.float32)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = Path(tmp.name)
            try:
                with wave.open(str(wav_path), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(audio.sample_rate)
                    wav.writeframes(
                        (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
                    )
                return self._run(["aplay", "-D", "pulse", str(wav_path)], block)
            finally:
                wav_path.unlink(missing_ok=True)
        except Exception:
            LOGGER.exception("sherpa-onnx voice prompt failed; falling back to system TTS")
            return False

    def _try_tts(self, text: str, block: bool | None) -> bool:
        candidates = [
            ["spd-say", text],
            ["espeak", text],
        ]
        for command in candidates:
            if shutil.which(command[0]) and self._run(command, block):
                return True
        return False

    def _run(self, command: list[str], block: bool | None) -> bool:
        try:
            should_block = self._config.block if block is None else block
            if should_block:
                subprocess.run(command, check=False)
            else:
                subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return True
        except Exception:
            LOGGER.exception("Voice prompt failed: %s", command)
            return False
