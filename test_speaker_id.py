from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np

from smart_lock.config import load_config
from smart_lock.speaker_id import (
    LocalMfccSpeakerAuthenticator,
    SpeakerAudioAccumulator,
    create_speaker_authenticator,
)


def main() -> None:
    config = load_config("config.yaml")
    with tempfile.TemporaryDirectory(prefix="smart-lock-speaker-test-") as tmpdir:
        root = Path(tmpdir)
        speaker_config = replace(
            config.speaker,
            backend="mfcc_local",
            voice_dir=str(root / "voices"),
            sample_rate=16000,
            min_speech_seconds=0.25,
            min_voice_samples=1,
        )

        test_accumulator(speaker_config)
        test_verify_audio(speaker_config)
        test_factory(speaker_config)
    print("speaker_id no-passphrase tests passed")


def test_accumulator(config) -> None:
    accumulator = SpeakerAudioAccumulator(config)
    assert accumulator.duration_seconds == 0.0
    assert not accumulator.has_enough()

    accumulator.add(np.zeros(8000, dtype=np.float32))  # 0.5 s at 16 kHz
    assert abs(accumulator.duration_seconds - 0.5) < 1e-6
    assert accumulator.has_enough()
    assert accumulator.collect() is not None
    assert accumulator.duration_seconds == 0.0
    assert accumulator.collect() is None
    accumulator.clear()


def test_verify_audio(config) -> None:
    authenticator = LocalMfccSpeakerAuthenticator(config)

    empty = authenticator.verify_audio(np.zeros(8000, dtype=np.float32))
    assert empty.name == "speaker"
    assert empty.passed is False
    assert empty.reason == "voice model not enrolled"

    person_dir = Path(config.voice_dir) / "张三"
    person_dir.mkdir(parents=True, exist_ok=True)
    (person_dir / "张三_001.json").write_text(
        json.dumps({"sample_rate": 16000, "embedding": [0.0] * (config.n_mfcc * 2)}),
        encoding="utf-8",
    )

    short = authenticator.verify_audio(np.zeros(1600, dtype=np.float32))  # 0.1 s
    assert short.passed is False
    assert "not enough voice audio" in short.reason

    rng = np.random.default_rng(42)
    enough = authenticator.verify_audio(rng.normal(0.0, 0.01, 8000).astype(np.float32))
    assert enough.name == "speaker"
    assert enough.score >= 0.0
    assert "unknown" in enough.reason


def test_factory(config) -> None:
    authenticator = create_speaker_authenticator(config)
    assert isinstance(authenticator, LocalMfccSpeakerAuthenticator)
    try:
        create_speaker_authenticator(replace(config, backend="unknown_backend"))
    except ValueError:
        pass
    else:
        raise AssertionError("unknown speaker backend must fail")


if __name__ == "__main__":
    main()
