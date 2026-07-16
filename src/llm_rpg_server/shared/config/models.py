from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PromptDefinition(BaseModel):
    prompt_id: str
    version: str
    system: str
    user: str = ""


class ContentDocument(BaseModel):
    schema_version: str
    data: dict[str, Any] = Field(default_factory=dict)

