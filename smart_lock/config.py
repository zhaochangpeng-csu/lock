from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import yaml


DEFAULT_VOICE_MESSAGES = {
    "app_started": "智能门锁已启动",
    "presence": "检测到有人靠近，请正对摄像头",
    "auto_start": "检测到红外和人脸，开始自动认证",
    "face_pass": "人脸识别通过",
    "face_fail": "人脸识别失败，请正对摄像头",
    "liveness_prompt": "请眨眼一次，并左右转头",
    "liveness_pass": "活体检测通过",
    "liveness_fail": "活体检测失败，请重新眨眼并转头",
    "voice_prompt": "请说出口令",
    "voice_pass": "声纹识别通过",
    "voice_fail": "声纹识别失败，请重新说出口令",
    "auth_pass": "认证通过，欢迎回家",
    "auth_fail": "认证失败，请重试",
}


@dataclass(frozen=True)
class SystemConfig:
    dry_run: bool
    log_level: str
    cooldown_seconds: float
    max_attempt_seconds: float


@dataclass(frozen=True)
class CameraConfig:
    index: int
    width: int
    height: int
    warmup_frames: int
    mock_when_unavailable_in_dry_run: bool


@dataclass(frozen=True)
class SensorSerialConfig:
    port_candidates: list[str]
    baudrate: int
    timeout_seconds: float
    command_hex: str
    response_bytes: int
    value_hex_start: int
    value_hex_end: int
    detected_value: int


@dataclass(frozen=True)
class SensorConfig:
    type: str
    pin: int
    active_high: bool
    poll_interval_seconds: float
    mock_trigger_interval_seconds: float
    serial: Optional[SensorSerialConfig] = None


@dataclass(frozen=True)
class LockConfig:
    relay_pin: int
    active_high: bool
    unlock_seconds: float


@dataclass(frozen=True)
class FaceConfig:
    enabled: bool
    backend: str
    min_score: float
    accept_any_detected_face_in_dry_run: bool
    accept_mock_frame_in_dry_run: bool
    haar_scale_factor: float
    haar_min_neighbors: int
    min_face_size: int
    data_dir: str
    model_path: str
    labels_path: str
    width: int
    height: int
    lbph_confidence_max: float
    insightface_model: str
    insightface_root: str
    insightface_det_size: list[int]
    insightface_ctx_id: int
    embedding_dir: str


@dataclass(frozen=True)
class LivenessConfig:
    enabled: bool
    backend: str
    min_score: float
    pass_in_dry_run: bool
    duration_seconds: float
    min_motion_ratio: float
    mediapipe_model_path: str
    min_blink_ear_drop: float
    min_head_yaw_motion: float


@dataclass(frozen=True)
class SpeakerConfig:
    enabled: bool
    backend: str
    min_score: float
    passphrase: str
    pass_in_dry_run: bool
    voice_dir: str
    input_device: int | None
    sample_rate: int
    record_seconds: float
    n_mfcc: int
    min_voice_samples: int
    speechbrain_source: str
    speechbrain_savedir: str


@dataclass(frozen=True)
class AutoAuthConfig:
    enabled: bool = True
    cooldown_seconds: float = 8.0
    require_sensor: bool = True
    require_face: bool = True
    presence_voice_cooldown_seconds: float = 6.0
    auto_start_delay_ms: int = 200


@dataclass(frozen=True)
class VoiceFeedbackConfig:
    enabled: bool = True
    backend: str = "aplay_pcm"
    pcm_dir: str = "audio_prompts"
    pcm_device: str = "plughw:Device"
    pcm_rate: int = 16000
    pcm_format: str = "S16_LE"
    pcm_channels: int = 1
    cooldown_seconds: float = 1.2
    block: bool = False
    fallback_to_tts_command: bool = True
    messages: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_VOICE_MESSAGES))


@dataclass(frozen=True)
class FusionConfig:
    threshold: float
    weights: dict[str, float]


@dataclass(frozen=True)
class AppConfig:
    system: SystemConfig
    camera: CameraConfig
    sensor: SensorConfig
    lock: LockConfig
    face: FaceConfig
    liveness: LivenessConfig
    speaker: SpeakerConfig
    auto_auth: AutoAuthConfig
    voice_feedback: VoiceFeedbackConfig
    fusion: FusionConfig


def load_config(path: Union[str, Path]) -> AppConfig:
    with Path(path).open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    sensor_raw = dict(raw["sensor"])
    serial_raw = sensor_raw.get("serial")
    sensor_raw["serial"] = SensorSerialConfig(**serial_raw) if serial_raw else None
    voice_raw = dict(raw.get("voice_feedback", {}))
    messages = dict(DEFAULT_VOICE_MESSAGES)
    messages.update(voice_raw.pop("messages", {}) or {})
    voice_raw["messages"] = messages

    return AppConfig(
        system=SystemConfig(**raw["system"]),
        camera=CameraConfig(**raw["camera"]),
        sensor=SensorConfig(**sensor_raw),
        lock=LockConfig(**raw["lock"]),
        face=FaceConfig(**raw["face"]),
        liveness=LivenessConfig(**raw["liveness"]),
        speaker=SpeakerConfig(**raw["speaker"]),
        auto_auth=AutoAuthConfig(**raw.get("auto_auth", {})),
        voice_feedback=VoiceFeedbackConfig(**voice_raw),
        fusion=FusionConfig(**raw["fusion"]),
    )
