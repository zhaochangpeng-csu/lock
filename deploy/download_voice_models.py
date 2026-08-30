from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smart_lock.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Preload voice agent ASR model")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--model", default=None)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    asr = config.agent.asr
    model_name = args.model or asr.model
    device = args.device or asr.device

    from funasr import AutoModel
    from funasr.download.name_maps_from_hub import name_maps_ms
    from modelscope import snapshot_download

    download_root = Path(asr.download_root)
    download_root.mkdir(parents=True, exist_ok=True)

    def download(model_id: str) -> str:
        resolved_id = name_maps_ms.get(model_id, model_id)
        local_dir = download_root / model_id.rstrip("/").rsplit("/", 1)[-1]
        snapshot_download(resolved_id, local_dir=str(local_dir))
        return str(local_dir)

    model_path = download(model_name)
    vad_path = download(asr.vad_model) if asr.vad_model else ""

    kwargs = {
        "model": model_path,
        "device": device,
        "disable_update": True,
    }
    if vad_path:
        kwargs["vad_model"] = vad_path
        kwargs["vad_kwargs"] = {"max_single_segment_time": 30000}
    AutoModel(**kwargs)
    print(f"voice model ready: model={model_path} vad={vad_path or 'none'} device={device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
