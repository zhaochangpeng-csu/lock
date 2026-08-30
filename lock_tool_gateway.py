from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from smart_lock.config import load_config
from smart_lock.auth_context import consume_auth_context, read_auth_context
from smart_lock.lock_gpio import JetsonRelayLockActuator


class QueryWhitelistRequest(BaseModel):
    person: str = ""


class NotifyOwnerRequest(BaseModel):
    message: str


class RequestUnlockRequest(BaseModel):
    reason: str


def _people_dirs(roots: list[Path]) -> set[str]:
    people: set[str] = set()
    for root in roots:
        if root.exists():
            people.update(path.name for path in root.iterdir() if path.is_dir())
    return people


def _last_jsonl_record(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    last_line = ""
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last_line = line
    if not last_line:
        return None
    return json.loads(last_line)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _log_tool_call(config, tool: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
    _append_jsonl(
        Path(config.agent.tool_gateway.call_log_path),
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool,
            "payload": payload,
            "result": result,
        },
    )


def _request_payload(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _auth_context_summary(
    record: dict[str, Any] | None, max_age_seconds: float
) -> dict[str, object]:
    if record is None:
        return {
            "available": False,
            "fresh": False,
            "authorized": False,
            "fusion_passed": False,
            "consumed": False,
            "credential_id": "",
            "reason": "no auth audit record yet",
        }

    raw_time = record.get("time") or record.get("timestamp")
    try:
        parsed_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        now = datetime.now(parsed_time.tzinfo) if parsed_time.tzinfo else datetime.now()
        age_seconds = (now - parsed_time).total_seconds()
    except (TypeError, ValueError):
        return {
            "available": True,
            "fresh": False,
            "authorized": False,
            "fusion_passed": False,
            "consumed": False,
            "credential_id": "",
            "reason": "invalid auth audit timestamp",
        }

    fusion_passed = bool(record.get("fusion_passed"))
    consumed = bool(record.get("consumed"))
    fresh = -2.0 <= age_seconds <= max_age_seconds
    reason = "hardware fusion passed" if fusion_passed else "hardware fusion rejected"
    if age_seconds < -2.0:
        reason = "auth audit timestamp is in the future"
    elif age_seconds > max_age_seconds:
        reason = "auth context expired"
    elif consumed:
        reason = "auth credential already consumed"

    return {
        "available": True,
        "fresh": fresh,
        "authorized": bool(fusion_passed and not consumed),
        "fusion_passed": bool(fusion_passed),
        "consumed": consumed,
        "credential_id": str(record.get("credential_id") or ""),
        "reason": reason,
        "age_seconds": round(max(age_seconds, 0.0), 3),
        "max_age_seconds": max_age_seconds,
        **(
            {"fusion_score": record["fusion_score"]}
            if "fusion_score" in record
            else {}
        ),
    }


def create_app(config_path: str = "config.yaml") -> FastAPI:
    config = load_config(config_path)
    token = os.getenv(config.agent.tool_gateway.token_env, "")
    app = FastAPI(title="Smart Lock Tool Gateway")
    unlock_lock = threading.Lock()
    actuator: JetsonRelayLockActuator | None = None

    def require_token(authorization: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(status_code=500, detail=f"{config.agent.tool_gateway.token_env} is not set")
        expected = f"Bearer {token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid token")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "service": "smart-lock-tool-gateway"}

    @app.get("/tools/current_auth_context", dependencies=[Depends(require_token)])
    def current_auth_context() -> dict[str, object]:
        record = read_auth_context(config.agent.safety.auth_context_path)
        result = _auth_context_summary(
            record, config.agent.safety.auth_context_max_age_seconds
        )
        _log_tool_call(config, "current_auth_context", {}, result)
        return result

    @app.post("/tools/query_whitelist", dependencies=[Depends(require_token)])
    def query_whitelist(request: QueryWhitelistRequest) -> dict[str, object]:
        people = sorted(
            _people_dirs(
                [
                    Path(config.face.embedding_dir),
                    Path(config.face.data_dir),
                    Path(config.speaker.voice_dir),
                ]
            )
        )
        person = request.person.strip()
        matched = [name for name in people if not person or person in name]
        result = {"allowed": bool(matched), "matches": matched, "registered_people": people}
        _log_tool_call(config, "query_whitelist", _request_payload(request), result)
        return result

    @app.post("/tools/notify_owner", dependencies=[Depends(require_token)])
    def notify_owner(request: NotifyOwnerRequest) -> dict[str, object]:
        result = {
            "sent": False,
            "backend": os.getenv("NOTIFY_OWNER_BACKEND", "mock"),
            "message": request.message,
            "reason": "notification backend is deferred",
        }
        _log_tool_call(config, "notify_owner", _request_payload(request), result)
        return result

    @app.post("/tools/request_unlock", dependencies=[Depends(require_token)])
    def request_unlock(request: RequestUnlockRequest) -> dict[str, object]:
        nonlocal actuator
        with unlock_lock:
            record = read_auth_context(config.agent.safety.auth_context_path)
            context = _auth_context_summary(
                record, config.agent.safety.auth_context_max_age_seconds
            )
            dry_run = os.getenv("SMART_LOCK_NO_UNLOCK", "1") != "0"
            allowed_by_fusion = bool(
                config.lock.flow == "agent_confirm"
                and context["available"]
                and context["fresh"]
                and context["authorized"]
            )
            allowed = allowed_by_fusion and not dry_run
            if allowed:
                credential_id = str(context["credential_id"])
                if actuator is None:
                    actuator = JetsonRelayLockActuator(config.lock)
                if not consume_auth_context(config.agent.safety.auth_context_path, credential_id):
                    allowed = False
                else:
                    actuator.unlock()
        result = {
            "allowed": allowed,
            "dry_run": dry_run,
            "request_reason": request.reason,
            "fusion_passed": context["fusion_passed"],
            "auth_context_fresh": context["fresh"],
            "message": (
                "unlock request accepted by safety gate"
                if allowed
                else (
                    "unlock request blocked by dry-run safety gate"
                    if allowed_by_fusion and dry_run
                    else f"unlock request rejected: {context['reason']}"
                )
            ),
        }
        _log_tool_call(config, "request_unlock", _request_payload(request), result)
        return result

    @app.on_event("shutdown")
    def close_actuator() -> None:
        if actuator is not None:
            actuator.close()

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart lock HTTP tool gateway")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", help="Override agent.tool_gateway.host or LOCK_TOOL_GATEWAY_HOST")
    parser.add_argument("--port", type=int, help="Override agent.tool_gateway.port or LOCK_TOOL_GATEWAY_PORT")
    args = parser.parse_args()

    config = load_config(args.config)
    import uvicorn

    uvicorn.run(
        create_app(args.config),
        host=args.host or config.agent.tool_gateway.host,
        port=args.port if args.port is not None else config.agent.tool_gateway.port,
    )


if __name__ == "__main__":
    main()
