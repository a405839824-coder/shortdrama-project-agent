from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_DELTA_ITEMS = 128
MAX_DELTA_STRING_CHARS = 20_000
MAX_DELTA_SERIALIZED_BYTES = 2 * 1024 * 1024
MAX_DELTA_NESTING_DEPTH = 12

SEMANTIC_AUDIT_LAYER_NAMES = (
    "源文对白",
    "机位可见性",
    "空间连续",
    "表演链",
    "镜头节奏",
    "正式母版单镜执行完整性",
    "禁项与声音",
    "统计闭合",
)

LEGACY_SEMANTIC_AUDIT_LAYER_ALIASES = {
    "Seedance独立执行": "正式母版单镜执行完整性",
}

SemanticAuditLayerName = Literal[
    "源文对白",
    "机位可见性",
    "空间连续",
    "表演链",
    "镜头节奏",
    "正式母版单镜执行完整性",
    "禁项与声音",
    "统计闭合",
]


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


class SemanticAuditLayerInput(BaseModel):
    """One independently reviewed semantic layer with concrete evidence."""

    model_config = ConfigDict(extra="forbid")

    layer: SemanticAuditLayerName
    status: Literal["pass", "fail_current"]
    summary: str = Field(min_length=1, max_length=4_000)
    evidence_ids: list[str] = Field(min_length=1, max_length=4096)

    @field_validator("layer", mode="before")
    @classmethod
    def normalize_legacy_layer(cls, value: Any) -> Any:
        return LEGACY_SEMANTIC_AUDIT_LAYER_ALIASES.get(str(value), value)

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: list[str]) -> list[str]:
        # Versioned envelope fragments may end inside a quoted JSON value.
        # Trimming that whitespace silently changes the audit observation.
        cleaned = []
        for value in values:
            value = value.lstrip()
            if not value.startswith(("audit-coverage-v1:", "audit-coverage-page-v1:")):
                value = value.rstrip()
            cleaned.append(value)
        if any(not value or len(value) > 256 for value in cleaned):
            raise ValueError("semantic_audit_evidence_invalid: evidence ids must be 1-256 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("semantic_audit_evidence_invalid: evidence ids must be unique per layer")
        if any(":" not in value for value in cleaned):
            raise ValueError(
                "semantic_audit_evidence_invalid: use concrete kind:id evidence references"
            )
        return cleaned


class SemanticAuditIssueInput(BaseModel):
    """One semantic defect found by the independent audit authority."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1, max_length=128)
    layer: SemanticAuditLayerName
    error_code: str = Field(min_length=1, max_length=128)
    severity: Literal["info", "warn", "fail", "blocking"] = "fail"
    blocking: bool = True
    shot_ids: list[str] = Field(default_factory=list, max_length=256)
    batch_ids: list[str] = Field(default_factory=list, max_length=256)
    field: str = Field(default="语义审计", min_length=1, max_length=256)
    issue_type: str | None = Field(default=None, min_length=1, max_length=256)
    problem_key: str | None = Field(default=None, min_length=1, max_length=512)
    root_cause: str | None = Field(default=None, min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(min_length=1, max_length=256)
    evidence: str = Field(min_length=1, max_length=8_000)
    repair_action: str = Field(min_length=1, max_length=4_000)

    @field_validator("layer", mode="before")
    @classmethod
    def normalize_legacy_layer(cls, value: Any) -> Any:
        return LEGACY_SEMANTIC_AUDIT_LAYER_ALIASES.get(str(value), value)

    @field_validator(
        "issue_id",
        "error_code",
        "field",
        "issue_type",
        "problem_key",
        "root_cause",
    )
    @classmethod
    def strip_identifiers(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("shot_ids", "batch_ids", "evidence_ids")
    @classmethod
    def validate_reference_lists(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values]
        if any(not value or len(value) > 256 for value in cleaned):
            raise ValueError("semantic_audit_reference_invalid: references must be 1-256 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("semantic_audit_reference_invalid: references must be unique")
        return cleaned

    @field_validator("batch_ids")
    @classmethod
    def reject_compact_batch_targets(cls, values: list[str]) -> list[str]:
        if values:
            raise ValueError(
                "semantic_audit_batch_removed: R10.1 issues target formal-master shots/fields, not batches"
            )
        return values

    @field_validator("evidence_ids")
    @classmethod
    def validate_issue_evidence_ids(cls, values: list[str]) -> list[str]:
        if any(":" not in value for value in values):
            raise ValueError(
                "semantic_audit_evidence_invalid: use concrete kind:id evidence references"
            )
        return values


class SemanticAuditConclusionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pass", "fail_current"]
    summary: str = Field(min_length=1, max_length=4_000)


class SemanticAuditObservationInput(BaseModel):
    """A literal, current field excerpt supporting an auditor's judgement."""

    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=1, max_length=256)
    quote: str = Field(max_length=2_000)


class SemanticAuditCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    obligation_id: str = Field(min_length=1, max_length=256)
    dependency_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["pass", "fail_current", "not_applicable", "unknown"]
    judgement: str = Field(min_length=12, max_length=2_000)
    observations: list[SemanticAuditObservationInput] = Field(min_length=1, max_length=64)
    issue_ids: list[str] = Field(default_factory=list, max_length=64)


class SemanticAuditCoverageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: Literal["semantic-obligation-coverage-v1"]
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    checks: list[SemanticAuditCheckInput] = Field(min_length=1, max_length=16_384)


class SemanticAuditCoveragePageInput(BaseModel):
    """Bound staging transport; it never represents an audit PASS."""
    model_config = ConfigDict(extra="forbid")
    contract: Literal["semantic-obligation-pages-v1"]
    action: Literal["append", "finalize"]
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    collection_key: str | None = Field(default=None, min_length=1, max_length=64)
    collection_id: str | None = Field(default=None, min_length=1, max_length=128)
    offset: int = Field(default=0, ge=0, le=16_384)
    checks: list[SemanticAuditCheckInput] = Field(default_factory=list, max_length=64)
    page_ids: list[str] = Field(default_factory=list, max_length=16_384)

    @model_validator(mode="after")
    def validate_operation(self) -> "SemanticAuditCoveragePageInput":
        if self.action == "append":
            if not self.checks or self.page_ids or bool(self.collection_key) == bool(self.collection_id):
                raise ValueError("audit_coverage_page_append_invalid")
            if self.collection_key and self.offset != 0:
                raise ValueError("audit_coverage_collection_must_start_at_zero")
        elif (not self.collection_id or self.collection_key or self.checks or self.offset or
              not self.page_ids or len(self.page_ids) != len(set(self.page_ids))):
            raise ValueError("audit_coverage_page_finalize_invalid")
        return self


class SemanticAuditInput(BaseModel):
    """Independent semantic review required on an uncached audit path.

    Service-level v4 validation additionally requires ordered pair evidence in
    空间连续 and every dialogue shot's ``field:shot_id.音色`` in 禁项与声音.
    """

    model_config = ConfigDict(extra="forbid")

    canonical_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    reviewed_shot_count: int = Field(ge=0)
    reviewed_adjacent_pair_count: int = Field(ge=0)
    adjacent_pair_order_sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    # R10.1 has no persisted Seedance batches.  The default keeps old callers
    # readable while making the compact contract natural to submit.
    reviewed_batch_count: Literal[0] = 0
    layers: list[SemanticAuditLayerInput] = Field(min_length=8, max_length=8)
    issues: list[SemanticAuditIssueInput] = Field(default_factory=list, max_length=512)
    conclusion: SemanticAuditConclusionInput
    # Optional at the transport boundary so an old window receives an actionable
    # service error, rather than an unexplainable cached-schema rejection.
    coverage: SemanticAuditCoverageInput | None = None
    coverage_page: SemanticAuditCoveragePageInput | None = None

    @model_validator(mode="after")
    def validate_complete_review(self) -> "SemanticAuditInput":
        layer_names = [row.layer for row in self.layers]
        if len(set(layer_names)) != len(layer_names) or set(layer_names) != set(
            SEMANTIC_AUDIT_LAYER_NAMES
        ):
            raise ValueError(
                "semantic_audit_layers_invalid: exactly eight unique R10 layers are required"
            )
        issue_ids = [row.issue_id for row in self.issues]
        if len(set(issue_ids)) != len(issue_ids):
            raise ValueError("semantic_audit_issue_invalid: issue_id values must be unique")
        return self


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
    # Deprecated compatibility input.  Validate its shape/size, then discard
    # it so an old caller can never reactivate background batch generation.
    replacement_batches: list[dict[str, Any]] = Field(
        default_factory=list, max_length=MAX_DELTA_ITEMS
    )
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
        self.replacement_batches = []
        return self
