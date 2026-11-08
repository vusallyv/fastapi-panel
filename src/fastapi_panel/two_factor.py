"""Time-based one-time codes (RFC 6238), the six digits an authenticator app shows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlencode

CODE_DIGITS = 6
STEP_SECONDS = 30
SECRET_BYTES = 20
# Accept the codes just before and after now, for phones whose clocks drift a little.
ALLOWED_DRIFT_STEPS = 1


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode()


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret)
    digest = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(number % 10**CODE_DIGITS).zfill(CODE_DIGITS)


def matching_step(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """The time step `code` belongs to, or None. Steps up to `last_step` are refused, so a code works once."""
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(current - ALLOWED_DRIFT_STEPS, current + ALLOWED_DRIFT_STEPS + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def setup_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?{urlencode({'secret': secret, 'issuer': issuer})}"


def qr_code_data_url(uri: str) -> str | None:
    """An SVG QR code, or None without the optional `qrcode` package (`pip install fastapi-panel[2fa]`)."""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError:
        return None
    svg = qrcode.make(uri, image_factory=SvgPathImage, border=2).to_string()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def display_secret(secret: str) -> str:
    """Groups of four, easier to type into an app by hand."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
