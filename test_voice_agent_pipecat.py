from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from loguru import logger
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from smart_lock.config import load_config
from voice_agent import _extract_stream_delta
from voice_agent_pipecat import TurnAudioCollector, _int16_frames_from_pcm, _pcm16_from_float32, _write_wav, run_pipeline


class RecordingSink(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)


def test_stream_parser() -> None:
    assert _extract_stream_delta('{"choices":[{"delta":{"content":"您好"}}]}') == "您好"
    assert _extract_stream_delta('{"choices":[{"delta":{"content":null}}]}') == ""
    assert _extract_stream_delta("not-json") == ""


def test_int16_output_conversion() -> None:
    frames = _int16_frames_from_pcm(b"\x00\x00\x01\x00\xff\xff")
    assert frames.dtype == np.int16
    assert frames.shape == (3, 1)
    assert frames[1, 0] == 1
    assert frames[2, 0] == -1


def test_audio_helpers() -> None:
    pcm = _pcm16_from_float32(np.array([0.0, 0.5, -0.5], dtype=np.float32))
    assert len(pcm) == 6
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "a.wav"
        _write_wav(path, pcm, 16000)
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getnframes() == 3


def test_turn_collector() -> None:
    logger.disable("pipecat")

    async def scenario() -> int:
        collector = TurnAudioCollector(sample_rate=16000, min_turn_seconds=0.1, pre_roll_seconds=0.1)
        await collector.process_frame(
            InputAudioRawFrame(audio=b"\x00\x00" * 1600, sample_rate=16000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        await collector.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await collector.process_frame(
            InputAudioRawFrame(audio=b"\x01\x00" * 3200, sample_rate=16000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        await collector.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert collector.turn_count == 1
        assert len(collector.last_turn_audio) > 0
        return collector.turn_count

    count = asyncio.run(scenario())
    logger.enable("pipecat")
    assert count == 1


def test_interruption_signal() -> None:
    logger.disable("pipecat")
    async def scenario() -> None:
        collector = TurnAudioCollector(sample_rate=16000, min_turn_seconds=0.1, pre_roll_seconds=0.1)
        await collector.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await collector.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        assert collector.interruption_count == 1

    asyncio.run(scenario())
    logger.enable("pipecat")


def test_e2e_file_pipeline() -> None:
    if os.getenv("RUN_PIPECAT_E2E") != "1":
        print("skip full Pipecat e2e (set RUN_PIPECAT_E2E=1 to run)")
        return
    config = load_config("config.yaml")
    with tempfile.TemporaryDirectory(prefix="smart-lock-pipecat-e2e-") as tmpdir:
        tmp = Path(tmpdir)
        input_wav = tmp / "input.wav"
        output_wav = tmp / "output.wav"
        sample = Path("models/funasr/SenseVoiceSmall/example/zh.mp3")
        if not sample.exists():
            print("skip: bundled ASR sample missing")
            return
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(sample), "-ar", "16000", "-ac", "1", str(input_wav)],
            check=True,
        )
        bytes_written = asyncio.run(
            run_pipeline(config, input_wav=input_wav, output_wav=output_wav, chat_id="pipecat-test")
        )
        assert bytes_written > 0
        with wave.open(str(output_wav), "rb") as wav:
            frames = wav.readframes(wav.getnframes())
        pcm = np.frombuffer(frames, dtype=np.int16)
        assert float(np.max(np.abs(pcm))) > 0
        print(f"pipecat e2e passed: {bytes_written} bytes of TTS audio")


if __name__ == "__main__":
    test_stream_parser()
    test_int16_output_conversion()
    test_audio_helpers()
    test_turn_collector()
    test_interruption_signal()
    test_e2e_file_pipeline()
    print("voice_agent_pipecat tests passed")
