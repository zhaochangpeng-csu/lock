from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from .config import VoiceFeedbackConfig

LOGGER = logging.getLogger(__name__)


class VoiceFeedback:
    def __init__(self, config: VoiceFeedbackConfig) -> None:
        self._config = config
        self._last_spoken: dict[str, float] = {}

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
