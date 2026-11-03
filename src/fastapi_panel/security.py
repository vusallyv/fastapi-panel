from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 128 * 1024 * 1024


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class Signer:
    """Tamper-proof (not encrypted) JSON tokens, used for the session cookie."""

    def __init__(self, secret: str, salt: str = "session") -> None:
        self._key = hashlib.sha256(f"{salt}:{secret}".encode()).digest()

    def _signature(self, payload: str) -> str:
        return _b64encode(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def dumps(self, data: object) -> str:
        payload = _b64encode(json.dumps(data, separators=(",", ":")).encode())
        return f"{payload}.{self._signature(payload)}"

    def loads(self, token: str) -> object | None:
        payload, _, signature = token.partition(".")
        if not hmac.compare_digest(self._signature(payload), signature):
            return None
        try:
            return json.loads(_b64decode(payload))
        except ValueError:
            return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = _b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(actual, expected)


# Verified against when a username does not exist, so response time doesn't reveal which do.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


class LoginThrottle:
    # shortcut: counters live in this process, use a shared store if you run many workers and need a strict limit
    def __init__(self, max_attempts: int = 5, window: int = 900, max_keys: int = 10_000) -> None:
        self.max_attempts = max_attempts
        self.window = window
        self.max_keys = max_keys
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        return [t for t in self._failures.get(key, []) if t > cutoff]

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_attempts

    def fail(self, key: str) -> None:
        if len(self._failures) >= self.max_keys:
            self._failures.clear()
        self._failures[key] = [*self._recent(key), time.monotonic()]

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}
