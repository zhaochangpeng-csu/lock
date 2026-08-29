from __future__ import annotations

import logging
import time
from typing import Protocol

from .config import SensorConfig

LOGGER = logging.getLogger(__name__)


class PresenceSensor(Protocol):
    def detected(self) -> bool:
        ...

    def close(self) -> None:
        ...


class MockInfraredSensor:
    def __init__(self, config: SensorConfig) -> None:
        self._interval = config.mock_trigger_interval_seconds
        self._last_trigger = 0.0
        LOGGER.warning("Using mock infrared sensor")

    def detected(self) -> bool:
        now = time.monotonic()
        if now - self._last_trigger >= self._interval:
            self._last_trigger = now
            return True
        return False

    def close(self) -> None:
        return None


class JetsonInfraredSensor:
    def __init__(self, config: SensorConfig) -> None:
        import Jetson.GPIO as GPIO

        self._GPIO = GPIO
        self._pin = config.pin
        self._active_high = config.active_high
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self._pin, GPIO.IN)
        LOGGER.info("Infrared sensor ready: pin=%s active_high=%s", self._pin, self._active_high)

    def detected(self) -> bool:
        value = bool(self._GPIO.input(self._pin))
        return value if self._active_high else not value

    def close(self) -> None:
        self._GPIO.cleanup(self._pin)


class SerialInfraredSensor:
    def __init__(self, config: SensorConfig) -> None:
        if config.serial is None:
            raise ValueError("serial_infrared requires sensor.serial config")

        import serial

        self._serial_config = config.serial
        self._command = bytes.fromhex(self._serial_config.command_hex)
        self._ser = None
        errors: list[str] = []

        for port in self._serial_config.port_candidates:
            try:
                self._ser = serial.Serial(
                    port,
                    baudrate=self._serial_config.baudrate,
                    timeout=self._serial_config.timeout_seconds,
                )
                LOGGER.info(
                    "Serial infrared sensor ready: port=%s baudrate=%s",
                    port,
                    self._serial_config.baudrate,
                )
                break
            except Exception as exc:
                errors.append(f"{port}: {exc}")

        if self._ser is None:
            raise RuntimeError("Cannot open serial infrared sensor: " + "; ".join(errors))

    def detected(self) -> bool:
        return self.read_value() == self._serial_config.detected_value

    def read_value(self) -> int | None:
        assert self._ser is not None
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        self._ser.write(self._command)
        response = self._ser.read(self._serial_config.response_bytes)
        hex_response = response.hex()

        if len(response) != self._serial_config.response_bytes:
            LOGGER.warning("Short infrared response: %s", hex_response or "<empty>")
            return None

        value = int(
            hex_response[
                self._serial_config.value_hex_start : self._serial_config.value_hex_end
            ],
            16,
        )
        LOGGER.debug("Infrared response=%s value=%s", hex_response, value)
        return value

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()


def create_presence_sensor(config: SensorConfig, dry_run: bool) -> PresenceSensor:
    if dry_run or config.type == "mock":
        return MockInfraredSensor(config)

    try:
        if config.type == "serial_infrared":
            return SerialInfraredSensor(config)
        if config.type == "gpio_infrared":
            return JetsonInfraredSensor(config)
        raise ValueError(f"Unsupported sensor type: {config.type}")
    except Exception:
        LOGGER.exception("Infrared sensor init failed; falling back to mock sensor")
        return MockInfraredSensor(config)
