from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    DataFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

from smart_lock.config import AppConfig, load_config
from voice_agent import FastGPTChatClient, FunASRTranscriber, _sounddevice_device

LOGGER = logging.getLogger(__name__)

VAD_START_SECS = 0.2
VAD_STOP_SECS = 1.3
VAD_MIN_TURN_SECONDS = 0.5
VAD_CONFIDENCE = 0.4
VAD_MIN_VOLUME = 0.02


@dataclass
class UserTurnAudioFrame(DataFrame):
    """PCM bytes captured for one VAD user turn."""

    audio: bytes
    sample_rate: int


@dataclass
class UserTranscriptFrame(DataFrame):
    text: str


@dataclass
class AssistantTranscriptFrame(DataFrame):
    text: str


@dataclass
class AssistantTurnCompleteFrame(DataFrame):
    text: str


def _pcm16_from_float32(audio: np.ndarray) -> bytes:
    mono = np.asarray(audio, dtype=np.float32).reshape(-1)
    return np.clip(mono * 32767.0, -32768, 32767).astype(np.int16).tobytes()


def _int16_frames_from_pcm(audio: bytes) -> np.ndarray:
    return np.frombuffer(audio, dtype=np.int16).reshape(-1, 1)


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


class SoundDeviceInputTransport(BaseInputTransport):
    """Pipecat input transport backed by sounddevice/PortAudio (16 kHz mono)."""

    def __init__(
        self,
        *,
        sample_rate: int,
        device: int | str | None = None,
        vad_analyzer: SileroVADAnalyzer | None = None,
        **kwargs,
    ) -> None:
        params = TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=sample_rate,
            audio_in_channels=1,
            audio_in_passthrough=True,
            vad_analyzer=vad_analyzer,
        )
        super().__init__(params, **kwargs)
        self._device = device
        self._in_stream = None

    async def start(self, frame: StartFrame):
        import sounddevice as sd

        await super().start(frame)
        if self._in_stream is not None:
            return
        self._in_stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            device=self._device,
            latency="high",
            callback=self._audio_callback,
        )
        self._in_stream.start()
        await self.set_transport_ready(frame)
        LOGGER.info("SoundDevice input started: device=%s rate=%s", self._device, self._sample_rate)

    async def cleanup(self):
        if self._in_stream is not None:
            self._in_stream.stop()
            self._in_stream.close()
            self._in_stream = None
        await super().cleanup()

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if indata.dtype == np.int16:
            audio = np.asarray(indata[:, 0], dtype=np.int16).tobytes()
        else:
            audio = _pcm16_from_float32(indata[:, 0])
        frame = InputAudioRawFrame(
            audio=audio,
            sample_rate=self._sample_rate,
            num_channels=1,
        )
        asyncio.run_coroutine_threadsafe(
            self.push_audio_frame(frame), self.get_event_loop()
        )


class SoundDeviceOutputTransport(BaseOutputTransport):
    """Pipecat output transport backed by sounddevice/PortAudio."""

    def __init__(self, *, sample_rate: int, device: int | str | None = None, **kwargs) -> None:
        params = TransportParams(
            audio_out_enabled=True,
            audio_out_sample_rate=sample_rate,
            audio_out_channels=1,
        )
        super().__init__(params, **kwargs)
        self._device = device
        self._out_stream = None

    async def start(self, frame: StartFrame):
        import sounddevice as sd

        await super().start(frame)
        if self._out_stream is not None:
            return
        self._out_stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            device=self._device,
            latency="high",
        )
        self._out_stream.start()
        await self.set_transport_ready(frame)
        LOGGER.info("SoundDevice output started: device=%s rate=%s", self._device, self._sample_rate)

    async def cleanup(self):
        if self._out_stream is not None:
            self._out_stream.stop()
            self._out_stream.close()
            self._out_stream = None
        await super().cleanup()

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        if self._out_stream is None:
            return False
        samples = _int16_frames_from_pcm(frame.audio)
        await self.get_event_loop().run_in_executor(None, self._out_stream.write, samples)
        return True


class WavFileInputTransport(BaseInputTransport):
    """Reads a 16 kHz mono WAV and pushes it as Pipecat input audio."""

    def __init__(
        self,
        *,
        wav_path: Path,
        sample_rate: int = 16000,
        vad_analyzer: SileroVADAnalyzer | None = None,
        tail_seconds: float = 0.8,
        end_silence_seconds: float = 1.0,
        **kwargs,
    ) -> None:
        params = TransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=sample_rate,
            audio_in_channels=1,
            audio_in_passthrough=True,
            vad_analyzer=vad_analyzer,
        )
        super().__init__(params, **kwargs)
        self._wav_path = wav_path
        self._tail_seconds = tail_seconds
        self._end_silence_seconds = end_silence_seconds
        self._reader: wave.Wave_read | None = None
        self._play_task: asyncio.Task | None = None

    async def start(self, frame: StartFrame):
        await super().start(frame)
        self._reader = wave.open(str(self._wav_path), "rb")
        if self._reader.getframerate() != self._sample_rate:
            raise RuntimeError(
                f"{self._wav_path} must be {self._sample_rate} Hz mono PCM, "
                f"got {self._reader.getframerate()} Hz"
            )
        await self.set_transport_ready(frame)
        self._play_task = asyncio.create_task(self._play())
        LOGGER.info("WAV input started: %s", self._wav_path)

    async def cleanup(self):
        if self._play_task is not None and not self._play_task.done():
            self._play_task.cancel()
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        await super().cleanup()

    async def _play(self) -> None:
        chunk_frames = int(self._sample_rate / 10)  # 100 ms chunks
        while True:
            data = self._reader.readframes(chunk_frames)
            if not data:
                break
            await self.push_audio_frame(
                InputAudioRawFrame(audio=data, sample_rate=self._sample_rate, num_channels=1)
            )
            await asyncio.sleep(0.1)

        silence_frames = int(self._sample_rate / 10)
        silence = b"\x00\x00" * silence_frames
        silence_chunks = int(self._end_silence_seconds * 10)
        for _ in range(silence_chunks):
            await self.push_audio_frame(
                InputAudioRawFrame(audio=silence, sample_rate=self._sample_rate, num_channels=1)
            )
            await asyncio.sleep(0.1)

        await asyncio.sleep(self._tail_seconds)
        LOGGER.info("WAV input playback finished: %s", self._wav_path)


class WavFileOutputTransport(BaseOutputTransport):
    """Collects Pipecat output audio into a WAV file (WSL test sink)."""

    def __init__(self, *, wav_path: Path, sample_rate: int = 16000, **kwargs) -> None:
        params = TransportParams(
            audio_out_enabled=True,
            audio_out_sample_rate=sample_rate,
            audio_out_channels=1,
        )
        super().__init__(params, **kwargs)
        self._wav_path = wav_path
        self._writer: wave.Wave_write | None = None
        self._bytes_written = 0

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    async def start(self, frame: StartFrame):
        await super().start(frame)
        self._writer = wave.open(str(self._wav_path), "wb")
        self._writer.setnchannels(1)
        self._writer.setsampwidth(2)
        self._writer.setframerate(self._sample_rate)
        self._bytes_written = 0
        await self.set_transport_ready(frame)

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    async def cleanup(self):
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        await super().cleanup()

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        if self._writer is None:
            return False
        self._writer.writeframes(frame.audio)
        self._bytes_written += len(frame.audio)
        return True


class TurnAudioCollector(FrameProcessor):
    """Buffers passthrough audio between Pipecat VAD turn events."""

    def __init__(
        self,
        *,
        sample_rate: int,
        min_turn_seconds: float = 0.2,
        pre_roll_seconds: float = 0.4,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sample_rate = sample_rate
        self._min_turn_seconds = min_turn_seconds
        self._pre_roll_bytes = int(sample_rate * pre_roll_seconds) * 2
        self._speaking = False
        self._bot_speaking = False
        self._buffer = bytearray()
        self._pre_roll = bytearray()
        self.turn_count = 0
        self.last_turn_audio = b""
        self.interruption_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (VADUserStartedSpeakingFrame, UserStartedSpeakingFrame)):
            LOGGER.debug("VAD user turn start")
            self._speaking = True
            self._buffer.clear()
            self._buffer.extend(self._pre_roll)
            await self.push_frame(frame, direction)
            if self._bot_speaking:
                self.interruption_count += 1
                await self.push_frame(InterruptionFrame(), direction)
            return

        if isinstance(frame, (VADUserStoppedSpeakingFrame, UserStoppedSpeakingFrame)):
            LOGGER.debug("VAD user turn stop, buffered=%.2fs", len(self._buffer) / 2 / self._sample_rate)
            self._speaking = False
            await self.push_frame(frame, direction)
            min_bytes = int(self._sample_rate * self._min_turn_seconds) * 2
            if len(self._buffer) >= min_bytes:
                self.last_turn_audio = bytes(self._buffer)
                self.turn_count += 1
                await self.push_frame(
                    UserTurnAudioFrame(audio=self.last_turn_audio, sample_rate=self._sample_rate),
                    direction,
                )
            else:
                LOGGER.info(
                    "Ignoring short VAD turn: %.2fs",
                    len(self._buffer) / 2 / self._sample_rate,
                )
            self._buffer.clear()
            return

        if isinstance(frame, InputAudioRawFrame):
            self._pre_roll.extend(frame.audio)
            overflow = len(self._pre_roll) - self._pre_roll_bytes
            if overflow > 0:
                del self._pre_roll[:overflow]
            if self._speaking:
                self._buffer.extend(frame.audio)
            return

        if isinstance(frame, InterruptionFrame):
            self._speaking = False
            self._buffer.clear()

        await self.push_frame(frame, direction)


class FunASRTranscriberProcessor(FrameProcessor):
    def __init__(
        self,
        config: AppConfig,
        *,
        transcriber: FunASRTranscriber | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._transcriber = transcriber
        self._task: asyncio.Task | None = None
        self._transcribe_lock = asyncio.Lock()

    def _get_transcriber(self) -> FunASRTranscriber:
        if self._transcriber is None:
            self._transcriber = FunASRTranscriber(self._config)
        return self._transcriber

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserTurnAudioFrame):
            self._task = asyncio.create_task(self._transcribe(frame))
            return
        if isinstance(frame, InterruptionFrame):
            if self._task is not None and not self._task.done():
                self._task.cancel()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (InputAudioRawFrame, UserTurnAudioFrame)):
            return
        await self.push_frame(frame, direction)

    async def _transcribe(self, frame: UserTurnAudioFrame) -> None:
        async with self._transcribe_lock:
            await self._transcribe_locked(frame)

    async def _transcribe_locked(self, frame: UserTurnAudioFrame) -> None:
        tmp = Path(tempfile.mktemp(suffix=".wav"))
        try:
            _write_wav(tmp, frame.audio, frame.sample_rate)
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, self._get_transcriber().transcribe, tmp)
            text = text.strip()
            LOGGER.info("ASR: %s", text)
            if text:
                await self.push_frame(UserTranscriptFrame(text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("FunASR transcription failed")
            await self.push_frame(UserTranscriptFrame(f"语音识别失败：{exc}"))
        finally:
            tmp.unlink(missing_ok=True)


class FastGPTChatProcessor(FrameProcessor):
    def __init__(self, config: AppConfig, *, chat_id: str = "smart-lock-pipecat", **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = FastGPTChatClient(config)
        self._chat_id = chat_id
        self._task: asyncio.Task | None = None
        self._reply_lock = asyncio.Lock()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserTranscriptFrame):
            self._task = asyncio.create_task(self._reply(frame.text))
            return
        if isinstance(frame, InterruptionFrame):
            if self._task is not None and not self._task.done():
                self._task.cancel()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (InputAudioRawFrame, UserTurnAudioFrame)):
            return
        await self.push_frame(frame, direction)

    async def _reply(self, text: str) -> None:
        async with self._reply_lock:
            await self._reply_locked(text)

    async def _reply_locked(self, text: str) -> None:
        chunks: list[str] = []
        try:
            async for delta in self._client.chat_stream_async(text, chat_id=self._chat_id):
                chunks.append(delta)
                await self.push_frame(AssistantTranscriptFrame(delta))
            full = "".join(chunks).strip()
            LOGGER.info("FastGPT full reply: %s", full)
            await self.push_frame(AssistantTurnCompleteFrame(full or "我没有听清，请再说一次。"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("FastGPT streaming failed")
            await self.push_frame(AssistantTurnCompleteFrame(f"语音 Agent 暂时不可用：{exc}"))


class EdgeTTSAudioProcessor(FrameProcessor):
    def __init__(self, config: AppConfig, *, sample_rate: int = 16000, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._sample_rate = sample_rate
        self._task: asyncio.Task | None = None
        self._synthesize_lock = asyncio.Lock()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AssistantTurnCompleteFrame):
            if frame.text.strip():
                self._task = asyncio.create_task(self._synthesize(frame.text))
            return
        if isinstance(frame, InterruptionFrame):
            if self._task is not None and not self._task.done():
                self._task.cancel()
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (InputAudioRawFrame, UserTurnAudioFrame, AssistantTranscriptFrame)):
            return
        await self.push_frame(frame, direction)

    async def _synthesize(self, text: str) -> None:
        async with self._synthesize_lock:
            await self._synthesize_locked(text)

    async def _synthesize_locked(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            pcm = await loop.run_in_executor(None, self._edge_tts_to_pcm, text)
            if pcm:
                await self.push_frame(
                    OutputAudioRawFrame(audio=pcm, sample_rate=self._sample_rate, num_channels=1)
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("EdgeTTS synthesis failed")

    def _edge_tts_to_pcm(self, text: str) -> bytes:
        import edge_tts

        proxy = os_getenv_proxy()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            mp3_path = Path(tmp.name)
        try:
            asyncio.run(
                edge_tts.Communicate(
                    text, self._config.agent.tts.voice, proxy=proxy
                ).save(str(mp3_path))
            )
            converted = subprocess.run(
                [
                    ffmpeg_executable(),
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(mp3_path),
                    "-f",
                    "s16le",
                    "-ar",
                    str(self._sample_rate),
                    "-ac",
                    "1",
                    "-",
                ],
                check=True,
                stdout=subprocess.PIPE,
            )
            return converted.stdout
        finally:
            mp3_path.unlink(missing_ok=True)


def os_getenv_proxy() -> str | None:
    import os

    return os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")


def ffmpeg_executable() -> str:
    """Return system ffmpeg, or the ffmpeg bundled with imageio-ffmpeg."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "ffmpeg not found in PATH and imageio-ffmpeg fallback is unavailable. "
            "Install ffmpeg (Jetson: apt install ffmpeg) or imageio-ffmpeg."
        ) from exc


def configure_pulse_default_source() -> None:
    """Match the verified Jetson microphone path: use PulseAudio default source."""
    if not sys.platform.startswith("linux") or not shutil.which("pactl"):
        return
    try:
        listing = subprocess.run(
            ["pactl", "list", "short", "sources"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout
        for line in listing.splitlines():
            if "XFM-DP" in line:
                index = line.split()[0]
                subprocess.run(["pactl", "set-default-source", index], check=True)
                LOGGER.info("PulseAudio default source set to XFM-DP source %s", index)
                return
        LOGGER.warning("XFM-DP source not found in pactl; keeping current default source")
    except Exception:
        LOGGER.exception("Failed to configure PulseAudio default source")


def create_pipeline_task(
    pipeline: Pipeline,
    *,
    params: PipelineParams,
    check_dangling_tasks: bool = False,
    enable_turn_tracking: bool = False,
    enable_tracing: bool = False,
) -> PipelineTask:
    """Create a PipelineTask compatible with Pipecat 0.0.100 and 0.0.108."""
    try:
        return PipelineTask(
            pipeline,
            params=params,
            check_dangling_tasks=check_dangling_tasks,
            enable_rtvi=False,
            enable_turn_tracking=enable_turn_tracking,
            enable_tracing=enable_tracing,
        )
    except TypeError:
        # Pipecat 0.0.100 and older do not have enable_rtvi.
        return PipelineTask(
            pipeline,
            params=params,
            check_dangling_tasks=check_dangling_tasks,
            enable_turn_tracking=enable_turn_tracking,
            enable_tracing=enable_tracing,
        )


def build_pipeline(
    config: AppConfig,
    *,
    input_transport: FrameProcessor,
    output_transport: FrameProcessor,
    chat_id: str,
    sample_rate: int = 16000,
    tts_sample_rate: int | None = None,
    transcriber: FunASRTranscriber | None = None,
) -> Pipeline:
    collector = TurnAudioCollector(
        sample_rate=sample_rate, min_turn_seconds=VAD_MIN_TURN_SECONDS
    )
    transcriber_processor = FunASRTranscriberProcessor(config, transcriber=transcriber)
    llm = FastGPTChatProcessor(config, chat_id=chat_id)
    tts = EdgeTTSAudioProcessor(
        config, sample_rate=tts_sample_rate or sample_rate
    )
    return Pipeline(
        [input_transport, collector, transcriber_processor, llm, tts, output_transport]
    )


async def run_realtime_pipeline(
    config: AppConfig,
    *,
    input_device: int | str | None = None,
    output_device: int | str | None = None,
    chat_id: str,
    sample_rate: int = 16000,
    output_sample_rate: int = 44100,
    transcriber: FunASRTranscriber | None = None,
) -> None:
    if input_device is None:
        configure_pulse_default_source()
    vad = SileroVADAnalyzer(
        params=VADParams(
            confidence=VAD_CONFIDENCE,
            start_secs=VAD_START_SECS,
            stop_secs=VAD_STOP_SECS,
            min_volume=VAD_MIN_VOLUME,
        )
    )
    input_transport = SoundDeviceInputTransport(
        sample_rate=sample_rate, device=input_device, vad_analyzer=vad
    )
    output_transport = SoundDeviceOutputTransport(
        sample_rate=output_sample_rate, device=output_device
    )
    pipeline = build_pipeline(
        config,
        input_transport=input_transport,
        output_transport=output_transport,
        chat_id=chat_id,
        sample_rate=sample_rate,
        tts_sample_rate=output_sample_rate,
        transcriber=transcriber,
    )
    task = create_pipeline_task(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            audio_in_sample_rate=sample_rate,
            audio_out_sample_rate=output_sample_rate,
        ),
    )

    @task.event_handler("on_pipeline_started")
    async def on_started(task, frame):
        await asyncio.sleep(0.8)
        await pipeline.push_frame(AssistantTurnCompleteFrame("认证通过，请说开门指令"))

    runner = PipelineRunner()
    await runner.run(task)


async def run_pipeline(config: AppConfig, *, input_wav: Path, output_wav: Path, chat_id: str) -> int:
    vad = SileroVADAnalyzer(
        params=VADParams(
            confidence=VAD_CONFIDENCE,
            start_secs=VAD_START_SECS,
            stop_secs=VAD_STOP_SECS,
            min_volume=VAD_MIN_VOLUME,
        )
    )
    input_transport = WavFileInputTransport(wav_path=input_wav, sample_rate=16000, vad_analyzer=vad)
    output_transport = WavFileOutputTransport(wav_path=output_wav, sample_rate=16000)
    pipeline = build_pipeline(
        config,
        input_transport=input_transport,
        output_transport=output_transport,
        chat_id=chat_id,
        sample_rate=16000,
    )
    task = create_pipeline_task(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=16000,
        ),
    )

    async def stop_when_audio_ready() -> None:
        deadline = asyncio.get_running_loop().time() + 120.0
        while output_transport.bytes_written == 0:
            if asyncio.get_running_loop().time() > deadline:
                LOGGER.error("Timed out waiting for pipeline TTS output")
                await task.cancel()
                return
            await asyncio.sleep(0.2)
        # Give the last output frames a moment to reach the file sink.
        await asyncio.sleep(0.5)
        LOGGER.info("Pipeline produced %s bytes of TTS audio; stopping", output_transport.bytes_written)
        await task.cancel()

    @task.event_handler("on_pipeline_finished")
    async def on_finished(task, frame):
        LOGGER.info("Pipeline finished")

    stop_task = asyncio.create_task(stop_when_audio_ready())
    runner = PipelineRunner()
    await runner.run(task)
    if not stop_task.done():
        stop_task.cancel()
    return output_transport.bytes_written


def wait_for_fresh_auth_context(config: AppConfig, timeout_seconds: float = 0.0) -> None:
    """Block until the GUI writes a fresh, unconsumed fusion credential."""
    from datetime import datetime, timezone

    from smart_lock.auth_context import read_auth_context

    path = config.agent.safety.auth_context_path
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    print(f"Waiting for hardware auth context: {path}")
    while True:
        record = read_auth_context(path)
        if record and record.get("fusion_passed") and not record.get("consumed"):
            raw_time = record.get("time") or record.get("timestamp")
            try:
                created = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - created).total_seconds()
                if 0 <= age <= config.agent.safety.auth_context_max_age_seconds:
                    print(f"Fresh auth context found (age={age:.1f}s)")
                    return
            except (TypeError, ValueError):
                pass
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("Timed out waiting for hardware auth context")
        time.sleep(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipecat continuous voice agent prototype")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--input-device", default=None, type=_sounddevice_device,
        help="sounddevice input device index or name (real-time mode)",
    )
    parser.add_argument(
        "--output-device", default=None, type=_sounddevice_device,
        help="sounddevice output device index or name (real-time mode)",
    )
    parser.add_argument("--input-wav", default=None, help="Test with a 16 kHz mono WAV input")
    parser.add_argument("--output-wav", default=None, help="Write generated TTS to this WAV")
    parser.add_argument("--chat-id", default="smart-lock-pipecat")
    parser.add_argument("--list-devices", action="store_true", help="List sounddevice audio devices")
    parser.add_argument(
        "--wait-auth",
        action="store_true",
        help="Preload FunASR and wait for a fresh hardware credential before opening the microphone",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list_devices:
        try:
            import sounddevice as sd
        except OSError as exc:
            print(f"No PortAudio available in this environment: {exc}")
            print("Run this command on Windows or Jetson to enumerate audio devices.")
            return 2
        print(sd.query_devices())
        return 0

    if args.input_wav and args.output_wav:
        bytes_written = asyncio.run(
            run_pipeline(
                config,
                input_wav=Path(args.input_wav),
                output_wav=Path(args.output_wav),
                chat_id=args.chat_id,
            )
        )
        print(f"pipeline output bytes={bytes_written} output_wav={args.output_wav}")
        return 0

    input_device = args.input_device
    output_device = args.output_device
    transcriber = None
    if args.wait_auth:
        print("Preloading FunASR model...")
        transcriber = FunASRTranscriber(config)
        transcriber.preload()
        wait_for_fresh_auth_context(config)
    print(f"Starting Pipecat real-time voice agent: input={input_device} output={output_device}")
    try:
        import sounddevice  # noqa: F401 - fail early without PortAudio.
    except OSError as exc:
        print(f"No PortAudio available in this environment: {exc}")
        print("Real-time mode must run on Windows or Jetson with working sounddevice/PortAudio.")
        return 2
    asyncio.run(
        run_realtime_pipeline(
            config,
            input_device=input_device,
            output_device=output_device,
            chat_id=args.chat_id,
            transcriber=transcriber,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
