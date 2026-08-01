"""HMAC helpers for per-command authentication (ha-cmd-hmac-v1)."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

MAX_TIMESTAMP_SKEW_SECONDS = 60


def build_canonical(fields: dict[str, str]) -> str:
    """Build the canonical string used for HMAC signing/verification."""
    parts = [
        f"{key}={value}"
        for key, value in sorted(fields.items(), key=lambda item: item[0])
        if key != "signature"
    ]
    return "\n".join(parts)


def sign(fields: dict[str, str], command_secret: str) -> str:
    canonical = build_canonical(fields)
    digest = hmac.new(
        command_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest.lower()


def _coerce_field_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def payload_to_fields(payload: dict[str, Any]) -> dict[str, str]:
    return {key: _coerce_field_value(value) for key, value in payload.items()}


def verify(
    payload: dict[str, Any],
    command_secret: str,
    *,
    expected_link_id: str | None = None,
    now: float | None = None,
) -> bool:
    """Verify a signed command payload.

    Returns False when the secret/link/signature/timestamp checks fail.
    """
    if not command_secret:
        return False

    fields = payload_to_fields(payload)
    signature = fields.get("signature", "").strip().lower()
    if not signature:
        return False

    link_id = fields.get("linkId", "")
    if expected_link_id is not None and link_id != expected_link_id:
        return False

    nonce = fields.get("nonce", "")
    if not nonce:
        return False

    timestamp_text = fields.get("timestamp", "")
    try:
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return False

    current = time.time() if now is None else now
    if abs(current - timestamp) > MAX_TIMESTAMP_SKEW_SECONDS:
        return False

    expected = sign(fields, command_secret)
    return hmac.compare_digest(expected, signature)
