"""HMAC signing/verification — the Python mirror of scheduler-service's
app/hmac_sig.py. Signature = hex(HMAC_SHA256(secret, f"{timestamp}.{raw_body}")),
empty body signs the empty string, 300s skew. Golden vectors are shared with the
service in vectors/hmac_vectors.json. Pure stdlib so it is importable in unit
tests without luna_sdk.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import time

SKEW_SECONDS = 300
HDR_TS = "x-sched-timestamp"
HDR_SIG = "x-sched-signature"


def sign(secret: str, raw_body: str, timestamp: str | None = None) -> tuple[str, str]:
    ts = timestamp or str(int(time.time()))
    mac = _hmac.new(
        secret.encode(), f"{ts}.{raw_body}".encode(), hashlib.sha256
    ).hexdigest()
    return ts, mac


def verify(secret: str, raw_body: str, timestamp: str | None, signature: str | None) -> bool:
    if not timestamp or not signature:
        return False
    try:
        skew = abs(int(time.time()) - int(timestamp))
    except (TypeError, ValueError):
        return False
    if skew > SKEW_SECONDS:
        return False
    expected = _hmac.new(
        secret.encode(), f"{timestamp}.{raw_body}".encode(), hashlib.sha256
    ).hexdigest()
    return _hmac.compare_digest(expected, signature)
