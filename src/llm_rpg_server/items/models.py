from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

UseContext = Literal["combat", "exploration", "world_event"]


class ItemUsePolicy(BaseModel):
    use_contexts: list[UseContext] = Field(default_factory=list)
    tradable: bool = True
    can_be_ingredient: bool = True
    category: str = "misc"
    tags: list[str] = Field(default_factory=list)


class ItemUseOutcome(BaseModel):
    item_id: str
    context: UseContext
    consumed: int = 1
    hp_restored: int = Field(default=0, ge=0)
    mp_restored: int = Field(default=0, ge=0)
    stamina_restored: int = Field(default=0, ge=0)
    cleared_statuses: int = Field(default=0, ge=0)
    applied_statuses: list[str] = Field(default_factory=list)


class EventItemOption(BaseModel):
    item_id: str
    name: str
    image_url: str = ""
    quantity: int = Field(ge=1)
    category: str
    tags: list[str] = Field(default_factory=list)
    match_score: int = Field(ge=0)
