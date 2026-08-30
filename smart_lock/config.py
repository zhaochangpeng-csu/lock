from __future__ import annotations

from dataclasses import dataclass, field
import os
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
    "voice_prompt": "请自然说话，完成声纹识别",
    "voice_pass": "声纹识别通过",
    "voice_fail": "声纹识别失败，请重新自然说一句话",
    "auth_pass": "认证通过，欢迎回家",
    "auth_fail": "认证失败，请重试",
}


@dataclass(frozen=True)
class SystemConfig:
    dry_run: bool
    log_level: str
    cooldown_seconds: float
    max_attempt_seconds: float
    result_hold_seconds: float = 5.0


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
    presence_hold_seconds: float = 3.0
    serial: Optional[SensorSerialConfig] = None


@dataclass(frozen=True)
class LockConfig:
    relay_pin: int
    active_high: bool
    unlock_seconds: float
    flow: str = "immediate"
    actuator: str = "relay"


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
    min_landmark_frames: int = 3
    mediapipe_min_face_detection_confidence: float = 0.4
    mediapipe_min_face_presence_confidence: float = 0.4
    mediapipe_min_tracking_confidence: float = 0.4


@dataclass(frozen=True)
class SpeakerConfig:
    enabled: bool
    backend: str
    min_score: float
    min_speech_seconds: float
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
    failed_retry_seconds: float = 2.0
    absence_rearm_seconds: float = 5.0
    require_sensor: bool = True
    require_face: bool = True
    presence_voice_cooldown_seconds: float = 6.0
    auto_start_delay_ms: int = 200
    camera_triggered_by_sensor: bool = True
    camera_idle_close_seconds: float = 10.0


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
    sherpa_model_dir: str = "models/tts/vits-piper-zh_CN-xiao_ya-medium-int8"
    sherpa_num_threads: int = 2
    sherpa_speed: float = 1.0
    messages: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_VOICE_MESSAGES))


@dataclass(frozen=True)
class FusionConfig:
    threshold: float
    weights: dict[str, float]
    require_all: bool = True


@dataclass(frozen=True)
class FastGPTConfig:
    api_base: str = "http://127.0.0.1:3000"
    api_base_env: str = "FASTGPT_API_BASE"
    app_api_key_env: str = "FASTGPT_APP_API_KEY"
    app_id_env: str = "FASTGPT_APP_ID"
    timeout_seconds: float = 30.0
    trust_env_proxy: bool = False


@dataclass(frozen=True)
class ToolGatewayConfig:
    host: str = "0.0.0.0"
    port: int = 8787
    host_env: str = "LOCK_TOOL_GATEWAY_HOST"
    port_env: str = "LOCK_TOOL_GATEWAY_PORT"
    token_env: str = "LOCK_TOOL_TOKEN"
    call_log_path: str = "logs/tool_gateway_calls.jsonl"
    call_log_path_env: str = "LOCK_TOOL_CALL_LOG"
    expose_to_lan: bool = True


@dataclass(frozen=True)
class AgentASRConfig:
    backend: str = "funasr"
    model: str = "iic/SenseVoiceSmall"
    vad_model: str = "fsmn-vad"
    download_root: str = "models/funasr"
    device: str = "cpu"
    record_seconds: float = 2.5


@dataclass(frozen=True)
class AgentTTSConfig:
    backend: str = "edge_tts"
    voice: str = "zh-CN-XiaoxiaoNeural"
    sherpa_model_dir: str = "models/tts/vits-piper-zh_CN-xiao_ya-medium-int8"
    sherpa_num_threads: int = 2
    sherpa_speed: float = 1.0


@dataclass(frozen=True)
class AgentSafetyConfig:
    auth_context_max_age_seconds: float = 60.0
    auth_context_path: str = "logs/auth_context.json"
    auth_context_path_env: str = "LOCK_AUTH_CONTEXT_PATH"


@dataclass(frozen=True)
class AgentConfig:
    system_prompt: str = (
        "You are a smart-lock voice agent at a door. Keep replies short and suitable for voice. "
        "Never call or invent a raw unlock function. "
        "Before any unlock request, call current_auth_context. "
        "Only call request_unlock(reason) when the context is available, fresh, authorized, and unconsumed."
    )
    fastgpt: FastGPTConfig = field(default_factory=FastGPTConfig)
    tool_gateway: ToolGatewayConfig = field(default_factory=ToolGatewayConfig)
    asr: AgentASRConfig = field(default_factory=AgentASRConfig)
    tts: AgentTTSConfig = field(default_factory=AgentTTSConfig)
    safety: AgentSafetyConfig = field(default_factory=AgentSafetyConfig)


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
    agent: AgentConfig


def load_config(path: Union[str, Path]) -> AppConfig:
    config_path = Path(path)
    _load_dotenv(config_path.parent / ".env")
    with config_path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    sensor_raw = dict(raw["sensor"])
    serial_raw = sensor_raw.get("serial")
    sensor_raw["serial"] = SensorSerialConfig(**serial_raw) if serial_raw else None
    voice_raw = dict(raw.get("voice_feedback", {}))
    messages = dict(DEFAULT_VOICE_MESSAGES)
    messages.update(voice_raw.pop("messages", {}) or {})
    voice_raw["messages"] = messages
    agent_raw = dict(raw.get("agent", {}))
    agent_fastgpt_raw = dict(agent_raw.pop("fastgpt", {}) or {})
    agent_tool_gateway_raw = dict(agent_raw.pop("tool_gateway", {}) or {})
    agent_asr_raw = dict(agent_raw.pop("asr", {}) or {})
    agent_tts_raw = dict(agent_raw.pop("tts", {}) or {})
    agent_safety_raw = dict(agent_raw.pop("safety", {}) or {})
    agent_raw["fastgpt"] = FastGPTConfig(**agent_fastgpt_raw)
    agent_raw["tool_gateway"] = ToolGatewayConfig(**agent_tool_gateway_raw)
    agent_raw["asr"] = AgentASRConfig(**agent_asr_raw)
    agent_raw["tts"] = AgentTTSConfig(**agent_tts_raw)
    agent_raw["safety"] = AgentSafetyConfig(**agent_safety_raw)
    agent_config = AgentConfig(**agent_raw)
    agent_config = _apply_agent_env_overrides(agent_config)

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
        agent=agent_config,
    )


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _apply_agent_env_overrides(config: AgentConfig) -> AgentConfig:
    fastgpt = config.fastgpt
    fastgpt_api_base = os.getenv(fastgpt.api_base_env)
    if fastgpt_api_base:
        fastgpt = FastGPTConfig(
            api_base=fastgpt_api_base,
            api_base_env=fastgpt.api_base_env,
            app_api_key_env=fastgpt.app_api_key_env,
            app_id_env=fastgpt.app_id_env,
            timeout_seconds=fastgpt.timeout_seconds,
            trust_env_proxy=fastgpt.trust_env_proxy,
        )

    gateway = config.tool_gateway
    gateway_host = os.getenv(gateway.host_env)
    gateway_port = _env_int(gateway.port_env)
    gateway_call_log_path = os.getenv(gateway.call_log_path_env)
    if gateway_host or gateway_port is not None or gateway_call_log_path:
        gateway = ToolGatewayConfig(
            host=gateway_host or gateway.host,
            port=gateway_port if gateway_port is not None else gateway.port,
            host_env=gateway.host_env,
            port_env=gateway.port_env,
            token_env=gateway.token_env,
            call_log_path=gateway_call_log_path or gateway.call_log_path,
            call_log_path_env=gateway.call_log_path_env,
            expose_to_lan=gateway.expose_to_lan,
        )

    safety = config.safety
    auth_context_path = os.getenv(safety.auth_context_path_env)
    if auth_context_path:
        safety = AgentSafetyConfig(
            auth_context_max_age_seconds=safety.auth_context_max_age_seconds,
            auth_context_path=auth_context_path,
            auth_context_path_env=safety.auth_context_path_env,
        )

    return AgentConfig(
        system_prompt=config.system_prompt,
        fastgpt=fastgpt,
        tool_gateway=gateway,
        asr=config.asr,
        tts=config.tts,
        safety=safety,
    )


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return int(value)
