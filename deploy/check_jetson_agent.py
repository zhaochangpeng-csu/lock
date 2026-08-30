from __future__ import annotations

import shutil
import sys


def main() -> int:
    print(f"python={sys.version.split()[0]} executable={sys.executable}")
    if sys.version_info < (3, 10):
        print("FAIL: Python >= 3.10 is required for the Pipecat voice agent.")
        return 1

    modules = {
        "pipecat": None,
        "onnxruntime": None,
        "numba": None,
        "soxr": None,
        "sounddevice": None,
        "edge_tts": None,
        "imageio_ffmpeg": None,
        "funasr": None,
        "requests": None,
    }
    failed = []
    for name in modules:
        try:
            module = __import__(name)
            modules[name] = str(getattr(module, "__version__", "installed"))
        except Exception as exc:
            modules[name] = f"MISSING: {type(exc).__name__}: {exc}"
            failed.append(name)

    for name, version in modules.items():
        print(f"{name}={version}")

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        print(f"system_ffmpeg={system_ffmpeg}")
    else:
        try:
            import imageio_ffmpeg

            print(f"ffmpeg_fallback={imageio_ffmpeg.get_ffmpeg_exe()}")
        except Exception as exc:
            failed.append("ffmpeg")
            print(f"ffmpeg_fallback=MISSING: {exc}")

    if failed:
        print("FAIL missing runtimes:", ", ".join(sorted(set(failed))))
        return 1
    print("OK: Jetson voice-agent runtime imports passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
