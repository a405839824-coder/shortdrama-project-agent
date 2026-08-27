from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
