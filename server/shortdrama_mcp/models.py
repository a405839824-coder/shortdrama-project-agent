from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_DELTA_ITEMS = 128
MAX_DELTA_STRING_CHARS = 20_000
MAX_DELTA_SERIALIZED_BYTES = 2 * 1024 * 1024
MAX_DELTA_NESTING_DEPTH = 12


def _check_delta_json_limits(value: Any, *, path: str = "delta", depth: int = 0) -> None:
    """Bound nested caller data before the merge layer deep-copies it."""

    if depth > MAX_DELTA_NESTING_DEPTH:
        raise ValueError(f"delta_input_too_deep: {path}")
    if isinstance(value, str):
        if len(value) > MAX_DELTA_STRING_CHARS:
            raise ValueError(f"delta_field_too_large: {path} exceeds {MAX_DELTA_STRING_CHARS} characters")
        return
    if isinstance(value, dict):
        if len(value) > 128:
            raise ValueError(f"delta_object_too_large: {path} has more than 128 fields")
        for key, item in value.items():
            key_text = str(key)
            if len(key_text) > 128:
                raise ValueError(f"delta_key_too_large: {path}")
            _check_delta_json_limits(item, path=f"{path}.{key_text}", depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError(f"delta_array_too_large: {path} has more than 256 items")
        for index, item in enumerate(value):
            _check_delta_json_limits(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if value is not None and not isinstance(value, (bool, int, float)):
        raise ValueError(f"delta_value_invalid: {path} is not JSON-compatible")


class OpenAIFile(BaseModel):
    """File object supplied by ChatGPT through an openai/fileParams field."""

    model_config = ConfigDict(extra="forbid")

    download_url: str
    file_id: str
    mime_type: str | None = None
    file_name: str | None = None


class ToolResponse(BaseModel):
    """Stable result envelope shared by all plugin tools."""

    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str
    project_id: str | None = None
    work_order_id: str | None = None
    artifact_id: str | None = None
    download_url: str | None = None
    trace_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


ReadUnit = Literal["auto", "characters", "paragraphs", "pages", "rows"]


class ArtifactReadRange(BaseModel):
    """One bounded range in a bulk artifact read.

    `offset` addresses the selected unit. `inner_offset` is only used when an
    unusually large paragraph or page must be resumed without losing text.
    """

    model_config = ConfigDict(extra="forbid")

    section: str | None = Field(default=None, max_length=256)
    offset: int = Field(default=0, ge=0)
    inner_offset: int = Field(default=0, ge=0)
    limit: int | None = Field(default=None, ge=1, le=16_000)
    unit: ReadUnit | None = None


class ChangeOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["add", "modify", "delete", "replace", "merge", "split", "move"]
    target_ids: list[str]
    instruction: str
    authoritative_basis: str
    protected_scope: list[str]


class StoryboardPatchOperationInput(BaseModel):
    """One audit-backed, non-structural repair to an existing shot field."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["replace_field"]
    shot_id: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=128)
    value: Any
    issue_ids: list[str] = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2_000)


class StoryboardDeltaEvidenceInput(BaseModel):
    """Affected-shot evidence supplied when an execution delta changes semantics.

    These are deliberately the only evidence blocks accepted by the delta
    route. Source mapping, protected dialogue, cognition, chapter boundaries,
    and global coverage must use the complete canonical route.
    """

    model_config = ConfigDict(extra="forbid")

    shot_plan: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_DELTA_ITEMS)
    performance_chains: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_DELTA_ITEMS)
    scene_state_ledger: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_DELTA_ITEMS)

    @model_validator(mode="after")
    def validate_nested_limits(self) -> "StoryboardDeltaEvidenceInput":
        _check_delta_json_limits(self.model_dump(mode="python"), path="evidence_updates")
        return self


class StoryboardDeltaRequestInput(BaseModel):
    """Strict pre-copy contract for one bounded, execution-only delta."""

    model_config = ConfigDict(extra="forbid")

    affected_shot_uids: list[str] = Field(min_length=1, max_length=MAX_DELTA_ITEMS)
    replacement_shots: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_DELTA_ITEMS)
    replacement_batches: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_DELTA_ITEMS)
    evidence_updates: StoryboardDeltaEvidenceInput = Field(
        default_factory=StoryboardDeltaEvidenceInput
    )

    @field_validator("affected_shot_uids")
    @classmethod
    def validate_uids(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values]
        if any(not value or len(value) > 128 for value in cleaned):
            raise ValueError("delta_scope_invalid: shot_uids must be 1-128 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("delta_scope_invalid: affected_shot_uids must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_payload_limits(self) -> "StoryboardDeltaRequestInput":
        value = self.model_dump(mode="python")
        _check_delta_json_limits(value)
        try:
            size = len(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("delta_value_invalid: request must be strict JSON") from exc
        if size > MAX_DELTA_SERIALIZED_BYTES:
            raise ValueError(
                f"delta_payload_too_large: {size} bytes exceeds {MAX_DELTA_SERIALIZED_BYTES}"
            )
        return self
