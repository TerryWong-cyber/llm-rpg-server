from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MonsterStats(BaseModel):
    hp: int = Field(gt=0)
    mp: int = Field(default=0, ge=0)
    strength: int = Field(default=1, ge=0)
    agility: int = Field(default=1, ge=0)
    intelligence: int = Field(default=1, ge=0)


class MonsterEquipment(BaseModel):
    weapon_id: str
    weapon_name: str
    armor_id: str
    armor_name: str
    item_id: str | None = None
    item_count: int = Field(default=0, ge=0)


class MonsterCombatProfile(BaseModel):
    threat: int = Field(default=1, ge=1, le=10)
    tactics: list[str] = Field(default_factory=list)
    arena: str
    forced: bool = False


class MonsterDrop(BaseModel):
    item_type: Literal["item", "material"]
    item_id: str
    chance: float = Field(ge=0, le=1)
    minimum: int = Field(default=1, ge=1)
    maximum: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "MonsterDrop":
        if self.maximum < self.minimum:
            raise ValueError("Monster drop maximum cannot be below minimum")
        return self


class MonsterDefinition(BaseModel):
    monster_id: str
    name: str
    title: str
    description: str
    emoji: str = "☠"
    rank: Literal["normal", "elite", "boss"] = "normal"
    image_url: str | None = None
    habitats: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    stats: MonsterStats
    equipment: MonsterEquipment
    combat: MonsterCombatProfile
    gold_min: int = Field(default=0, ge=0)
    gold_max: int = Field(default=0, ge=0)
    drops: list[MonsterDrop] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gold(self) -> "MonsterDefinition":
        if self.gold_max < self.gold_min:
            raise ValueError("Monster gold maximum cannot be below minimum")
        return self

    def public_view(self) -> dict:
        return self.model_dump(mode="json")
