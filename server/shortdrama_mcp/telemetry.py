from __future__ import annotations

import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator


_SAFE_STAGE_RE = re.compile(r"[^a-z0-9_.-]+")
_SAFE_METRIC_KEYS = {
    "artifact_count",
    "batch_count",
    "bytes_read",
    "bytes_written",
    "cache_hit",
    "char_count",
    "chunk_count",
    "item_count",
    "kind",
    "limit",
    "legacy_generation_calls",
    "materialization_passes",
    "model_authored_derived_chars",
    "not_modified",
    "offset",
    "page_count",
    "range_count",
    "returned_chars",
    "row_count",
    "seedance_batches_generated",
    "shot_count",
    "duplicate_generated_bytes",
    "truncated",
    "unit",
}
_SAFE_TEXT_VALUES = {
    "auto",
    "characters",
    "paragraphs",
    "pages",
    "rows",
    "text",
    "workbook",
    "table",
    "json",
    "ok",
    "pass",
    "not_modified",
    "compact_master_v1",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_stage_name(value: str) -> str:
    normalized = _SAFE_STAGE_RE.sub("-", str(value).strip().casefold()).strip("-.")
    return normalized[:64] or "stage"


def _safe_metrics(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if key not in _SAFE_METRIC_KEYS:
            continue
        if value is None or isinstance(value, (bool, int, float)):
            safe[key] = value
        elif isinstance(value, str) and value in _SAFE_TEXT_VALUES:
            safe[key] = value
    return safe


class WorkflowTrace:
    """Small, content-free stage timer suitable for inclusion in tool results."""

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or f"TRC-{uuid.uuid4().hex[:16]}"
        self.started_at = _utc_now()
        self._started_ns = time.perf_counter_ns()
        self._finished_ns: int | None = None
        self._stages: list[dict[str, Any]] = []

    @contextmanager
    def stage(self, name: str, **metrics: Any) -> Iterator[dict[str, Any]]:
        started = time.perf_counter_ns()
        mutable_metrics = dict(metrics)
        status = "ok"
        try:
            yield mutable_metrics
        except Exception:
            status = "error"
            raise
        finally:
            duration_ns = max(0, time.perf_counter_ns() - started)
            self._stages.append(
                {
                    "stage": _safe_stage_name(name),
                    "duration_ms": round(duration_ns / 1_000_000, 3),
                    "status": status,
                    "metrics": _safe_metrics(mutable_metrics),
                }
            )

    def finish(self) -> None:
        if self._finished_ns is None:
            self._finished_ns = time.perf_counter_ns()

    def as_dict(self) -> dict[str, Any]:
        self.finish()
        assert self._finished_ns is not None
        return {
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "duration_ms": round(max(0, self._finished_ns - self._started_ns) / 1_000_000, 3),
            "stages": list(self._stages),
        }
