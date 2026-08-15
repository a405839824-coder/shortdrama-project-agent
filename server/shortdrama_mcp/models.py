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
    data: dict[str, Any] = Field(default_factory=dict)


class ChangeOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["add", "modify", "delete", "replace", "merge", "split", "move"]
    target_ids: list[str]
    instruction: str
    authoritative_basis: str
    protected_scope: list[str]
