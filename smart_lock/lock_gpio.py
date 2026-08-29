from __future__ import annotations

import logging
import time
from typing import Protocol

from .config import LockConfig

LOGGER = logging.getLogger(__name__)


class LockActuator(Protocol):
    def unlock(self) -> None:
        ...

    def close(self) -> None:
        ...


class DryRunLockActuator:
    def __init__(self, config: LockConfig) -> None:
        self._unlock_seconds = config.unlock_seconds
        LOGGER.warning("Using dry-run lock actuator")

    def unlock(self) -> None:
        LOGGER.info("DRY RUN: unlock for %.2f seconds", self._unlock_seconds)

    def close(self) -> None:
        return None


class JetsonRelayLockActuator:
    def __init__(self, config: LockConfig) -> None:
        import Jetson.GPIO as GPIO

        self._GPIO = GPIO
        self._pin = config.relay_pin
        self._active_high = config.active_high
        self._unlock_seconds = config.unlock_seconds
        self._inactive_value = GPIO.LOW if self._active_high else GPIO.HIGH
        self._active_value = GPIO.HIGH if self._active_high else GPIO.LOW

        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self._pin, GPIO.OUT, initial=self._inactive_value)
        LOGGER.info("Relay lock ready: pin=%s active_high=%s", self._pin, self._active_high)

    def unlock(self) -> None:
        LOGGER.info("Unlock relay pulse: %.2f seconds", self._unlock_seconds)
        self._GPIO.output(self._pin, self._active_value)
        time.sleep(self._unlock_seconds)
        self._GPIO.output(self._pin, self._inactive_value)

    def close(self) -> None:
        self._GPIO.output(self._pin, self._inactive_value)
        self._GPIO.cleanup(self._pin)


def create_lock_actuator(config: LockConfig, dry_run: bool) -> LockActuator:
    if dry_run:
        return DryRunLockActuator(config)

    try:
        return JetsonRelayLockActuator(config)
    except Exception:
        LOGGER.exception("Jetson GPIO lock init failed; falling back to dry-run actuator")
        return DryRunLockActuator(config)

