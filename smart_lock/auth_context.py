from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .fusion import FusionDecision


def write_auth_context(path: str, decision: FusionDecision) -> dict[str, Any]:
    record = {
        "credential_id": uuid4().hex,
        "time": datetime.now(timezone.utc).isoformat(),
        "fusion_passed": bool(decision.passed),
        "fusion_score": float(decision.score),
        "consumed": False,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return record


def read_auth_context(path: str) -> dict[str, Any] | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def consume_auth_context(path: str, credential_id: str) -> bool:
    record = read_auth_context(path)
    if record is None or record.get("credential_id") != credential_id or record.get("consumed"):
        return False
    record["consumed"] = True
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return True
