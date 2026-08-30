from __future__ import annotations

import logging
import math
import os
import time
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .config import LivenessConfig
from .results import AuthResult

LOGGER = logging.getLogger(__name__)


class LivenessChecker(Protocol):
    def verify(self, frame: np.ndarray, dry_run: bool) -> AuthResult:
        ...


class MotionLivenessChecker:
    """Single-frame fallback; GUI runs the real short motion challenge."""

    def __init__(self, config: LivenessConfig) -> None:
        self._config = config

    def verify(self, frame: np.ndarray, dry_run: bool) -> AuthResult:
        if dry_run and self._config.pass_in_dry_run:
            return AuthResult("liveness", True, 1.0, "dry-run liveness accepted")

        LOGGER.warning("Motion liveness requires the GUI frame sequence")
        return AuthResult("liveness", False, 0.0, "motion liveness requires GUI")


class MediaPipeLivenessChecker:
    def __init__(self, config: LivenessConfig) -> None:
        self._config = config
        model_path = Path(config.mediapipe_model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"MediaPipe model not found: {model_path}. "
                "Download face_landmarker.task into this path."
            )

        os.environ.setdefault("MPLBACKEND", "Agg")
        import mediapipe as mp

        self._mp = mp
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=self._config.mediapipe_min_face_detection_confidence,
            min_face_presence_confidence=self._config.mediapipe_min_face_presence_confidence,
            min_tracking_confidence=self._config.mediapipe_min_tracking_confidence,
        )
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    def verify(self, frame: np.ndarray, dry_run: bool) -> AuthResult:
        if dry_run and self._config.pass_in_dry_run:
            return AuthResult("liveness", True, 1.0, "dry-run liveness accepted")
        result = self._landmarks(frame, int(time.time() * 1000))
        if result is None:
            return AuthResult("liveness", False, 0.0, "no face landmarks")
        return AuthResult("liveness", False, 0.0, "liveness needs frame sequence")

    def verify_frames(self, frames: list[np.ndarray]) -> AuthResult:
        if not frames:
            return AuthResult("liveness", False, 0.0, "no frames")

        ears: list[float] = []
        yaws: list[float] = []
        timestamp_ms = int(time.time() * 1000)
        for index, frame in enumerate(frames):
            landmarks = self._landmarks(frame, timestamp_ms + index * 40)
            if landmarks is None:
                continue
            ears.append((self._eye_aspect_ratio(landmarks, "left") + self._eye_aspect_ratio(landmarks, "right")) / 2.0)
            yaws.append(self._yaw_proxy(landmarks))

        if len(ears) < self._config.min_landmark_frames or len(yaws) < self._config.min_landmark_frames:
            return AuthResult(
                "liveness",
                False,
                0.0,
                f"not enough face landmarks: {max(len(ears), len(yaws))} < {self._config.min_landmark_frames}",
            )

        ear_drop = max(ears) - min(ears)
        yaw_motion = max(yaws) - min(yaws)
        blink_score = min(1.0, ear_drop / self._config.min_blink_ear_drop)
        yaw_score = min(1.0, yaw_motion / self._config.min_head_yaw_motion)
        score = 0.5 * blink_score + 0.5 * yaw_score
        passed = score >= self._config.min_score
        reason = f"blink_drop={ear_drop:.3f} yaw_motion={yaw_motion:.3f}"
        return AuthResult(
            "liveness",
            passed,
            float(score),
            reason,
            {"blink_drop": ear_drop, "yaw_motion": yaw_motion},
        )

    def close(self) -> None:
        self._landmarker.close()

    def _landmarks(self, frame: np.ndarray, timestamp_ms: int):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        if not result.face_landmarks:
            return None
        return result.face_landmarks[0]

    @staticmethod
    def _eye_aspect_ratio(landmarks, side: str) -> float:
        if side == "left":
            points = [33, 160, 158, 133, 153, 144]
        else:
            points = [362, 385, 387, 263, 373, 380]
        p1, p2, p3, p4, p5, p6 = [landmarks[i] for i in points]
        vertical = MediaPipeLivenessChecker._dist(p2, p6) + MediaPipeLivenessChecker._dist(p3, p5)
        horizontal = 2.0 * MediaPipeLivenessChecker._dist(p1, p4)
        return vertical / horizontal if horizontal > 0 else 0.0

    @staticmethod
    def _yaw_proxy(landmarks) -> float:
        left_eye = landmarks[33]
        right_eye = landmarks[263]
        nose = landmarks[1]
        eye_center_x = (left_eye.x + right_eye.x) / 2.0
        eye_width = abs(right_eye.x - left_eye.x)
        return (nose.x - eye_center_x) / eye_width if eye_width > 0 else 0.0

    @staticmethod
    def _dist(a, b) -> float:
        return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def create_liveness_checker(config: LivenessConfig) -> LivenessChecker:
    if config.backend == "mediapipe":
        try:
            return MediaPipeLivenessChecker(config)
        except Exception:
            LOGGER.exception("MediaPipe liveness unavailable; falling back to motion liveness")
            return MotionLivenessChecker(config)
    if config.backend in {"mvp_prompt", "motion"}:
        return MotionLivenessChecker(config)
    raise ValueError(f"Unsupported liveness backend: {config.backend}")
