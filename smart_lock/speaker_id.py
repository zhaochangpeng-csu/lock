from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

import numpy as np
from scipy.fftpack import dct

from .config import SpeakerConfig
from .results import AuthResult

LOGGER = logging.getLogger(__name__)


class SpeakerAuthenticator(Protocol):
    def verify(self, dry_run: bool) -> AuthResult:
        ...


class MvpPassphraseSpeakerAuthenticator:
    """Console passphrase placeholder for WeSpeaker/SpeechBrain verification."""

    def __init__(self, config: SpeakerConfig) -> None:
        self._config = config

    def verify(self, dry_run: bool) -> AuthResult:
        if dry_run and self._config.pass_in_dry_run:
            return AuthResult("speaker", True, 1.0, "dry-run speaker accepted")

        try:
            phrase = input("Say/type passphrase: ").strip()
        except EOFError:
            return AuthResult("speaker", False, 0.0, "no passphrase input")

        passed = phrase.lower() == self._config.passphrase.lower()
        return self.verify_phrase(phrase)

    def verify_phrase(self, phrase: str) -> AuthResult:
        passed = phrase.strip().lower() == self._config.passphrase.lower()
        score = 1.0 if passed else 0.0
        reason = "passphrase matched" if passed else "passphrase mismatch"
        return AuthResult("speaker", passed, score, reason)


class LocalMfccSpeakerAuthenticator:
    def __init__(self, config: SpeakerConfig) -> None:
        self._config = config
        Path(config.voice_dir).mkdir(parents=True, exist_ok=True)

    def verify(self, dry_run: bool) -> AuthResult:
        if dry_run and self._config.pass_in_dry_run:
            return AuthResult("speaker", True, 1.0, "dry-run speaker accepted")
        return self.verify_microphone()

    def enroll_microphone(self, name: str) -> Path:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("name is required")
        audio = self._record_audio()
        embedding = self._extract_embedding(audio)
        person_dir = Path(self._config.voice_dir) / clean_name
        person_dir.mkdir(parents=True, exist_ok=True)
        save_path = person_dir / f"{clean_name}_{len(list(person_dir.glob('*.json'))) + 1:03d}.json"
        save_path.write_text(
            json.dumps({"sample_rate": self._config.sample_rate, "embedding": embedding.tolist()}),
            encoding="utf-8",
        )
        LOGGER.info("Saved voice sample: %s", save_path)
        return save_path

    def verify_microphone(self) -> AuthResult:
        profiles = self._load_profiles()
        if not profiles:
            return AuthResult("speaker", False, 0.0, "voice model not enrolled")

        audio = self._record_audio()
        embedding = self._extract_embedding(audio)
        best_name = "unknown"
        best_score = 0.0

        for name, samples in profiles.items():
            centroid = np.mean(np.asarray(samples, dtype=np.float32), axis=0)
            score = self._cosine_score(embedding, centroid)
            if score > best_score:
                best_name = name
                best_score = score

        passed = best_score >= self._config.min_score
        reason = f"recognized {best_name}" if passed else f"unknown or low confidence ({best_name})"
        return AuthResult("speaker", passed, float(best_score), reason, {"identity": best_name})

    def people(self) -> list[str]:
        root = Path(self._config.voice_dir)
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())

    def sample_count(self, name: str) -> int:
        person_dir = Path(self._config.voice_dir) / name.strip()
        if not person_dir.exists():
            return 0
        return len(list(person_dir.glob("*.json")))

    def _record_audio(self) -> np.ndarray:
        import sounddevice as sd

        frames = int(self._config.sample_rate * self._config.record_seconds)
        audio = sd.rec(
            frames,
            samplerate=self._config.sample_rate,
            channels=1,
            dtype="float32",
            device=self._config.input_device,
        )
        sd.wait()
        audio = audio.reshape(-1)
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        if peak > 0:
            audio = audio / peak
        return audio

    def _load_profiles(self) -> dict[str, list[np.ndarray]]:
        root = Path(self._config.voice_dir)
        profiles: dict[str, list[np.ndarray]] = {}
        if not root.exists():
            return profiles

        for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            samples: list[np.ndarray] = []
            for sample_path in sorted(person_dir.glob("*.json")):
                raw = json.loads(sample_path.read_text(encoding="utf-8"))
                samples.append(np.asarray(raw["embedding"], dtype=np.float32))
            if len(samples) >= self._config.min_voice_samples:
                profiles[person_dir.name] = samples
        return profiles

    def _extract_embedding(self, audio: np.ndarray) -> np.ndarray:
        sample_rate = self._config.sample_rate
        emphasized = np.append(audio[0], audio[1:] - 0.97 * audio[:-1]) if len(audio) > 1 else audio
        frame_size = int(0.025 * sample_rate)
        frame_step = int(0.010 * sample_rate)
        signal_length = len(emphasized)
        num_frames = max(1, int(np.ceil(float(abs(signal_length - frame_size)) / frame_step)))
        pad_length = num_frames * frame_step + frame_size
        padded = np.append(emphasized, np.zeros(max(0, pad_length - signal_length)))

        indices = (
            np.tile(np.arange(0, frame_size), (num_frames, 1))
            + np.tile(np.arange(0, num_frames * frame_step, frame_step), (frame_size, 1)).T
        )
        frames = padded[indices.astype(np.int32, copy=False)]
        frames *= np.hamming(frame_size)

        nfft = 512
        power = (1.0 / nfft) * (np.absolute(np.fft.rfft(frames, nfft)) ** 2)
        filters = self._mel_filter_bank(sample_rate, nfft, nfilt=26)
        energies = np.dot(power, filters.T)
        energies = np.where(energies == 0, np.finfo(float).eps, energies)
        mfcc = dct(np.log(energies), type=2, axis=1, norm="ortho")[:, : self._config.n_mfcc]

        embedding = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)])
        norm = np.linalg.norm(embedding)
        return embedding / norm if norm > 0 else embedding

    @staticmethod
    def _mel_filter_bank(sample_rate: int, nfft: int, nfilt: int) -> np.ndarray:
        low_mel = 0
        high_mel = 2595 * np.log10(1 + (sample_rate / 2) / 700)
        mel_points = np.linspace(low_mel, high_mel, nfilt + 2)
        hz_points = 700 * (10 ** (mel_points / 2595) - 1)
        bins = np.floor((nfft + 1) * hz_points / sample_rate).astype(int)
        fbank = np.zeros((nfilt, int(np.floor(nfft / 2 + 1))))
        for m in range(1, nfilt + 1):
            left, center, right = bins[m - 1], bins[m], bins[m + 1]
            for k in range(left, center):
                fbank[m - 1, k] = (k - left) / max(1, center - left)
            for k in range(center, right):
                fbank[m - 1, k] = (right - k) / max(1, right - center)
        return fbank

    @staticmethod
    def _cosine_score(a: np.ndarray, b: np.ndarray) -> float:
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            return 0.0
        cosine = float(np.dot(a, b) / denom)
        return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


class SpeechBrainSpeakerAuthenticator(LocalMfccSpeakerAuthenticator):
    def __init__(self, config: SpeakerConfig) -> None:
        super().__init__(config)
        self._classifier = None

    def ensure_model(self) -> None:
        self._get_classifier()

    def enroll_microphone(self, name: str) -> Path:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("name is required")
        audio = self._record_audio()
        embedding = self._extract_speechbrain_embedding(audio)
        person_dir = Path(self._config.voice_dir) / clean_name
        person_dir.mkdir(parents=True, exist_ok=True)
        save_path = person_dir / f"{clean_name}_{len(list(person_dir.glob('*.npy'))) + 1:03d}.npy"
        np.save(str(save_path), embedding)
        LOGGER.info("Saved SpeechBrain voice sample: %s", save_path)
        return save_path

    def verify_microphone(self) -> AuthResult:
        profiles = self._load_speechbrain_profiles()
        if not profiles:
            return AuthResult("speaker", False, 0.0, "voice model not enrolled")

        audio = self._record_audio()
        embedding = self._extract_speechbrain_embedding(audio)
        best_name = "unknown"
        best_score = 0.0

        for name, samples in profiles.items():
            centroid = np.mean(np.asarray(samples, dtype=np.float32), axis=0)
            centroid = self._normalize(centroid)
            score = self._cosine_score(embedding, centroid)
            if score > best_score:
                best_name = name
                best_score = score

        passed = best_score >= self._config.min_score
        reason = f"recognized {best_name}" if passed else f"unknown or low confidence ({best_name})"
        return AuthResult("speaker", passed, float(best_score), reason, {"identity": best_name})

    def sample_count(self, name: str) -> int:
        person_dir = Path(self._config.voice_dir) / name.strip()
        if not person_dir.exists():
            return 0
        return len(list(person_dir.glob("*.npy"))) + len(list(person_dir.glob("*.json")))

    def _load_speechbrain_profiles(self) -> dict[str, list[np.ndarray]]:
        root = Path(self._config.voice_dir)
        profiles: dict[str, list[np.ndarray]] = {}
        if not root.exists():
            return profiles

        for person_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            samples = [self._normalize(np.load(str(path))) for path in sorted(person_dir.glob("*.npy"))]
            if len(samples) >= self._config.min_voice_samples:
                profiles[person_dir.name] = samples
        return profiles

    def _extract_speechbrain_embedding(self, audio: np.ndarray) -> np.ndarray:
        import torch

        self._patch_torch_amp(torch)
        classifier = self._get_classifier()
        waveform = torch.as_tensor(audio, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            embedding = classifier.encode_batch(waveform)
        array = embedding.squeeze().detach().cpu().numpy().astype(np.float32)
        return self._normalize(array)

    def _get_classifier(self):
        if self._classifier is not None:
            return self._classifier
        import torch

        self._patch_torch_amp(torch)
        from speechbrain.inference.speaker import EncoderClassifier

        self._classifier = EncoderClassifier.from_hparams(
            source=self._config.speechbrain_source,
            savedir=self._config.speechbrain_savedir,
            run_opts={"device": "cpu"},
        )
        return self._classifier

    @staticmethod
    def _patch_torch_amp(torch_module) -> None:
        amp = getattr(torch_module, "amp", None)
        cuda_amp = getattr(getattr(torch_module, "cuda", None), "amp", None)
        if amp is None or cuda_amp is None:
            return

        if not hasattr(amp, "custom_fwd") and hasattr(cuda_amp, "custom_fwd"):
            def custom_fwd(*args, **kwargs):
                kwargs.pop("device_type", None)
                return cuda_amp.custom_fwd(*args, **kwargs)

            amp.custom_fwd = custom_fwd

        if not hasattr(amp, "custom_bwd") and hasattr(cuda_amp, "custom_bwd"):
            def custom_bwd(*args, **kwargs):
                kwargs.pop("device_type", None)
                return cuda_amp.custom_bwd(*args, **kwargs)

            amp.custom_bwd = custom_bwd

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector


def create_speaker_authenticator(config: SpeakerConfig) -> SpeakerAuthenticator:
    if config.backend == "mvp_passphrase":
        return MvpPassphraseSpeakerAuthenticator(config)
    if config.backend == "speechbrain_ecapa":
        try:
            return SpeechBrainSpeakerAuthenticator(config)
        except Exception:
            LOGGER.exception("SpeechBrain unavailable; falling back to local MFCC speaker backend")
            return LocalMfccSpeakerAuthenticator(config)
    if config.backend == "mfcc_local":
        return LocalMfccSpeakerAuthenticator(config)
    raise ValueError(f"Unsupported speaker backend: {config.backend}")
