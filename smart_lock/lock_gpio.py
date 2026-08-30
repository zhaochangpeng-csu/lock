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
        try:
            time.sleep(self._unlock_seconds)
        finally:
            self._GPIO.output(self._pin, self._inactive_value)

    def close(self) -> None:
        self._GPIO.output(self._pin, self._inactive_value)
        self._GPIO.cleanup(self._pin)


class FanModbusLockActuator:
    """Uses a Modbus serial fan to simulate unlocking when no real lock is installed."""

    OPEN_CMD = bytes.fromhex("02 05 00 00 FF 00 8C 09")
    CLOSE_CMD = bytes.fromhex("02 05 00 00 00 00 CD F9")

    def __init__(self, config: LockConfig) -> None:
        self._config = config
        self._ser = None

    def _open_serial(self):
        if self._ser is not None:
            return self._ser
        import serial

        errors = []
        for port in ("/dev/ttyUSB0", "/dev/ttyUSB1"):
            try:
                self._ser = serial.Serial(port, baudrate=9600, timeout=0.5)
                LOGGER.info("Fan Modbus serial opened: %s", port)
                return self._ser
            except Exception as exc:
                errors.append(f"{port}: {exc}")
        raise RuntimeError("Cannot open fan Modbus serial: " + "; ".join(errors))

    def unlock(self) -> None:
        ser = self._open_serial()
        ser.flushInput()
        ser.flushOutput()
        ser.write(self.OPEN_CMD)
        LOGGER.info("Unlock pulse ON for %.2f seconds", self._config.unlock_seconds)
        time.sleep(self._config.unlock_seconds)
        ser.flushInput()
        ser.flushOutput()
        ser.write(self.CLOSE_CMD)
        LOGGER.info("Unlock pulse OFF")
        self.close()

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None


def create_lock_actuator(config: LockConfig, dry_run: bool) -> LockActuator:
    if dry_run:
        return DryRunLockActuator(config)

    if config.actuator == "fan_serial":
        try:
            return FanModbusLockActuator(config)
        except Exception:
            LOGGER.exception("Serial unlock actuator init failed; falling back to dry-run actuator")
            return DryRunLockActuator(config)

    try:
        return JetsonRelayLockActuator(config)
    except Exception:
        LOGGER.exception("Jetson GPIO lock init failed; falling back to dry-run actuator")
        return DryRunLockActuator(config)
