from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
DEFAULT_EVENT_PATH = ROOT / "latest_event.json"


class EventInput(BaseModel):
    event_type: str = Field(description="owner, known_visitor, stranger, or abnormal_behavior")
    identity: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    occurred_at: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any] | None:
        with self._lock:
            return self._read_unlocked()

    def record(self, value: EventInput) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        event = {
            "id": uuid4().hex,
            "event_type": value.event_type,
            "identity": value.identity,
            "confidence": value.confidence,
            "occurred_at": value.occurred_at or now,
            "recorded_at": now,
            "details": value.details,
            "processed": False,
            "processed_at": None,
        }
        with self._lock:
            self._write_unlocked(event)
        return event

    def mark_processed(self, event_id: str) -> dict[str, Any]:
        with self._lock:
            event = self._read_unlocked()
            if event is None:
                raise FileNotFoundError("latest_event.json does not exist")
            if event_id and event.get("id") != event_id:
                raise ValueError("event id does not match the latest event")
            event["processed"] = True
            event["processed_at"] = datetime.now(timezone.utc).isoformat()
            self._write_unlocked(event)
            return event

    def _read_unlocked(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read {self.path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid event document: {self.path}")
        return value

    def _write_unlocked(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(event, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def event_path() -> Path:
    return Path(os.getenv("LOCK_EVENT_PATH", str(DEFAULT_EVENT_PATH))).expanduser().resolve()


def create_app(path: Path | None = None) -> FastAPI:
    store = EventStore(path or event_path())
    token = os.getenv("LOCK_EVENT_SERVICE_TOKEN", "")
    app = FastAPI(title="Smart Lock Latest Event Service")

    def require_token(authorization: str) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "event_path": str(store.path)}

    @app.get("/event")
    def get_event(authorization: str = Header(default="")) -> dict[str, Any]:
        require_token(authorization)
        return {"event": store.read()}

    @app.post("/event")
    def record_event(value: EventInput, authorization: str = Header(default="")) -> dict[str, Any]:
        require_token(authorization)
        return {"event": store.record(value)}

    @app.post("/event/{event_id}/processed")
    def mark_processed(event_id: str, authorization: str = Header(default="")) -> dict[str, Any]:
        require_token(authorization)
        try:
            event = store.mark_processed(event_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"event": event}

    return app


app = create_app()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart lock latest event service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="start the HTTP service")
    serve.add_argument("--host", default=os.getenv("LOCK_EVENT_SERVICE_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("LOCK_EVENT_SERVICE_PORT", "8790")))

    record = subparsers.add_parser("record", help="record one latest event")
    record.add_argument("--event-type", required=True)
    record.add_argument("--identity", default="unknown")
    record.add_argument("--confidence", type=float, default=0.0)
    record.add_argument("--occurred-at")
    record.add_argument("--details", default="{}", help="JSON object")

    subparsers.add_parser("show", help="print the latest event")
    processed = subparsers.add_parser("processed", help="mark the latest event processed")
    processed.add_argument("--id", default="")

    args = parser.parse_args()
    store = EventStore(event_path())
    if args.command == "serve":
        import uvicorn

        uvicorn.run(create_app(store.path), host=args.host, port=args.port)
        return 0
    if args.command == "record":
        details = json.loads(args.details)
        if not isinstance(details, dict):
            raise ValueError("--details must be a JSON object")
        event = store.record(
            EventInput(
                event_type=args.event_type,
                identity=args.identity,
                confidence=args.confidence,
                occurred_at=args.occurred_at,
                details=details,
            )
        )
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0
    if args.command == "show":
        print(json.dumps(store.read(), ensure_ascii=False, indent=2))
        return 0
    event = store.mark_processed(args.id)
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
