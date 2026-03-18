"""Authentication and rate limiting for the WebUI."""

import hmac
import time

from pydantic import BaseModel, ConfigDict


class AuthResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    reason: str | None = None


_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60


class AuthRateLimiter:
    def __init__(
        self,
        *,
        max_failures: int = _MAX_FAILURES,
        lockout_seconds: int = _LOCKOUT_SECONDS,
    ) -> None:
        self._max_failures = max_failures
        self._lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}

    def is_blocked(self, ip: str) -> bool:
        attempts = self._failures.get(ip, [])
        if not attempts:
            return False
        now = time.monotonic()
        recent = [t for t in attempts if now - t < self._lockout_seconds]
        if not recent:
            del self._failures[ip]
            return False
        self._failures[ip] = recent
        return len(recent) >= self._max_failures

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        if ip not in self._failures:
            self._failures[ip] = []
        self._failures[ip].append(now)
        self._failures[ip] = [
            t for t in self._failures[ip] if now - t < self._lockout_seconds
        ]

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)


def verify_api_key(key: str, expected: str) -> bool:
    return hmac.compare_digest(key.encode(), expected.encode())
