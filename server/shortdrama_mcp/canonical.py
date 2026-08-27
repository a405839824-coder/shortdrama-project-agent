from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any


def _normalize_string(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def normalize_json(value: Any) -> Any:
    """Return a deterministic, JSON-safe representation without changing array order.

    The helper is deliberately strict: unsupported Python objects fail instead of
    being stringified, because silently stringifying them would make a cache key
    look stable when it is not.
    """

    if hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not allow NaN or infinity")
        return int(value) if value.is_integer() else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical JSON does not allow NaN or infinity")
        integral = value.to_integral_value()
        return int(integral) if value == integral else float(value)
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _normalize_string(str(raw_key))
            if key in normalized:
                raise ValueError(f"canonical JSON key collision after normalization: {key!r}")
            normalized[key] = normalize_json(raw_value)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize_json(item) for item in value]
    raise TypeError(f"value is not JSON serializable for canonical hashing: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    normalized = normalize_json(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def stable_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_sha256(value: Any) -> str:
    """Readable alias used by the MCP response and certificate contracts."""

    return stable_json_hash(value)


def normalize_etag(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:].strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
        candidate = candidate[1:-1]
    return candidate or None


def etag_matches(candidate: str | None, current: str | None) -> bool:
    left = normalize_etag(candidate)
    right = normalize_etag(current)
    return left is not None and right is not None and left == right
