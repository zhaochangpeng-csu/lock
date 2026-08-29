from __future__ import annotations

import argparse

import numpy as np
import sounddevice as sd

from smart_lock.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a short microphone sample")
    parser.add_argument("--seconds", type=float, default=1.0)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    config = load_config("config.yaml")
    device = args.device if args.device is not None else config.speaker.input_device
    frames = int(config.speaker.sample_rate * args.seconds)

    print(f"recording seconds={args.seconds} sample_rate={config.speaker.sample_rate} device={device}")
    audio = sd.rec(
        frames,
        samplerate=config.speaker.sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
    print(f"shape={audio.shape} peak={peak:.6f} rms={rms:.6f}")


if __name__ == "__main__":
    main()

