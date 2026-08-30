from __future__ import annotations

import logging
import time

from .camera import Camera
from .config import AppConfig
from .face_id import create_face_authenticator
from .fusion import FusionEngine
from .liveness import create_liveness_checker
from .lock_gpio import create_lock_actuator
from .results import AuthResult
from .sensor import create_presence_sensor
from .speaker_id import create_speaker_authenticator
from .auth_context import write_auth_context

LOGGER = logging.getLogger(__name__)


class SmartLockController:
    def __init__(self, config: AppConfig, force_lock_dry_run: bool = False) -> None:
        self._config = config
        self._camera = Camera(config.camera, config.system.dry_run)
        self._sensor = create_presence_sensor(config.sensor, config.system.dry_run)
        defer_unlock = config.lock.flow == "agent_confirm"
        self._lock = create_lock_actuator(
            config.lock,
            config.system.dry_run or force_lock_dry_run or defer_unlock,
        )
        self._face = create_face_authenticator(config.face)
        self._liveness = create_liveness_checker(config.liveness)
        self._speaker = create_speaker_authenticator(config.speaker)
        self._fusion = FusionEngine(config.fusion)

    def run_forever(self) -> None:
        LOGGER.info("Smart lock started; dry_run=%s", self._config.system.dry_run)
        try:
            while True:
                if self._sensor.detected():
                    self._handle_presence_event()
                    time.sleep(self._config.system.cooldown_seconds)
                time.sleep(self._config.sensor.poll_interval_seconds)
        except KeyboardInterrupt:
            LOGGER.info("Interrupted by user")
        finally:
            self.close()

    def run_once(self) -> None:
        LOGGER.info("Smart lock single attempt; dry_run=%s", self._config.system.dry_run)
        self._handle_presence_event()

    def close(self) -> None:
        self._camera.close()
        self._sensor.close()
        self._lock.close()

    def _handle_presence_event(self) -> None:
        LOGGER.info("Infrared presence detected")
        started_at = time.monotonic()
        results: list[AuthResult] = [AuthResult("sensor", True, 1.0, "presence detected")]

        try:
            frame = self._camera.read()
        except Exception as exc:
            LOGGER.exception("Camera capture failed")
            results.append(AuthResult("face", False, 0.0, f"camera failed: {exc}"))
            self._log_denied(results, 0.0)
            return

        if self._config.face.enabled:
            results.append(self._face.verify(frame, self._config.system.dry_run))
        if self._config.liveness.enabled and self._time_left(started_at):
            results.append(self._liveness.verify(frame, self._config.system.dry_run))
        if self._config.speaker.enabled and self._time_left(started_at):
            results.append(self._speaker.verify(self._config.system.dry_run))

        decision = self._fusion.decide(results)
        write_auth_context(self._config.agent.safety.auth_context_path, decision)
        if decision.passed:
            if self._config.lock.flow == "immediate":
                LOGGER.info("Authentication passed: fusion_score=%.3f", decision.score)
                self._lock.unlock()
            else:
                LOGGER.info(
                    "Authentication passed; waiting for Agent unlock request: fusion_score=%.3f",
                    decision.score,
                )
        else:
            self._log_denied(results, decision.score)

    def _time_left(self, started_at: float) -> bool:
        elapsed = time.monotonic() - started_at
        return elapsed < self._config.system.max_attempt_seconds

    @staticmethod
    def _log_denied(results: list[AuthResult], fusion_score: float) -> None:
        detail = ", ".join(
            f"{result.name}=pass:{result.passed}/score:{result.score:.2f}/{result.reason}"
            for result in results
        )
        LOGGER.warning("Authentication denied: fusion_score=%.3f; %s", fusion_score, detail)
