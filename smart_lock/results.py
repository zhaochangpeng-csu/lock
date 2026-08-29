from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthResult:
    name: str
    passed: bool
    score: float
    reason: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

