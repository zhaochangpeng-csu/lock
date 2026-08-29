from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from .config import CameraConfig

LOGGER = logging.getLogger(__name__)


class Camera:
    def __init__(self, config: CameraConfig, dry_run: bool) -> None:
        self._config = config
        self._dry_run = dry_run
        self._capture: Optional[cv2.VideoCapture] = None
        self._mock = False

    def open(self) -> None:
        capture = cv2.VideoCapture(self._config.index)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._config.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._config.height)

        if not capture.isOpened() and self._dry_run and self._config.mock_when_unavailable_in_dry_run:
            capture.release()
            self._mock = True
            LOGGER.warning("Camera unavailable; using dry-run mock frame")
            return

        if not capture.isOpened():
            raise RuntimeError(f"Cannot open camera index {self._config.index}")

        self._capture = capture
        for _ in range(self._config.warmup_frames):
            self.read()
        LOGGER.info("Camera opened: index=%s", self._config.index)

    def read(self) -> np.ndarray:
        if self._capture is None:
            self.open()
        if self._mock:
            return self._mock_frame()
        assert self._capture is not None

        ok, frame = self._capture.read()
        if not ok or frame is None:
            raise RuntimeError("Failed to read camera frame")
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
            LOGGER.info("Camera closed")

    def _mock_frame(self) -> np.ndarray:
        frame = np.zeros((self._config.height, self._config.width, 3), dtype=np.uint8)
        frame[:, :] = (40, 40, 40)
        cv2.circle(
            frame,
            (self._config.width // 2, self._config.height // 2),
            min(self._config.width, self._config.height) // 5,
            (180, 180, 180),
            -1,
        )
        return frame

    def __enter__(self) -> "Camera":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
