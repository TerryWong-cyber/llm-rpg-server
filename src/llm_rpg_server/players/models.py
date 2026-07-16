from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Inventory(BaseModel):
    weapons: list[str] = Field(default_factory=list)
    armors: list[str] = Field(default_factory=list)
    items: dict[str, int] = Field(default_factory=dict)
    materials: dict[str, int] = Field(default_factory=dict)


class PlayerProfile(BaseModel):
    player_id: str
    name: str
    character_id: str
    gold: int = Field(ge=0)
    inventory: Inventory
    current_map: dict[str, Any] | None = None

