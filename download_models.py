from __future__ import annotations

from pathlib import Path

import requests

from smart_lock.config import load_config


MEDIAPIPE_FACE_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
INSIGHTFACE_MODEL_URLS = {
    "buffalo_sc": [
        "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_sc.zip",
        "https://sourceforge.net/projects/insightface.mirror/files/v0.7/buffalo_sc.zip/download",
    ],
    "buffalo_s": [
        "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip",
        "https://sourceforge.net/projects/insightface.mirror/files/v0.7/buffalo_s.zip/download",
    ],
    "buffalo_l": [
        "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "https://sourceforge.net/projects/insightface.mirror/files/v0.7/buffalo_l.zip/download",
    ],
}


def main() -> None:
    config = load_config("config.yaml")
    _download_file(MEDIAPIPE_FACE_LANDMARKER_URL, Path(config.liveness.mediapipe_model_path), "MediaPipe")

    insightface_zip = (
        Path(config.face.insightface_root).expanduser()
        / "models"
        / f"{config.face.insightface_model}.zip"
    )
    insightface_dir = insightface_zip.with_suffix("")
    if insightface_dir.exists() and list(insightface_dir.glob("*.onnx")):
        print(f"InsightFace model exists: {insightface_dir}")
    else:
        _download_first_available(
            INSIGHTFACE_MODEL_URLS[config.face.insightface_model],
            insightface_zip,
            "InsightFace",
        )


def _download_first_available(urls: list[str], path: Path, label: str) -> None:
    last_error: Exception | None = None
    for url in urls:
        try:
            _download_file(url, path, label)
            return
        except Exception as exc:
            last_error = exc
            print(f"{label} download failed from {url}: {exc}")
            part_path = path.with_suffix(path.suffix + ".part")
            if part_path.exists():
                part_path.unlink()
    raise RuntimeError(f"All {label} download URLs failed") from last_error


def _download_file(url: str, path: Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        print(f"{label} file exists: {path} ({path.stat().st_size} bytes)")
        return

    print(f"Downloading {label}: {url}")
    with requests.get(url, stream=True, timeout=(10, 30)) as response:
        response.raise_for_status()
        temp_path = path.with_suffix(path.suffix + ".part")
        with temp_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    print(f"  {temp_path.stat().st_size // 1024} KB", end="\r")
        temp_path.replace(path)
    print(f"\nDownloaded {label}: {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
