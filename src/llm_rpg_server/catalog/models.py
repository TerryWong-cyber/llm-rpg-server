from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RaceAttributes(BaseModel):
    vitality: int = Field(ge=1, le=20)
    strength: int = Field(ge=1, le=20)
    agility: int = Field(ge=1, le=20)
    wisdom: int = Field(ge=1, le=20)
    luck: int = Field(ge=1, le=20)


class RaceBirthplace(BaseModel):
    world_id: str
    region_id: str
    template_id: str
    cell_id: int = Field(ge=0)
    settlement_name: str


class RaceSkill(BaseModel):
    id: str
    name: str
    desc: str
    cost: int = Field(default=0, ge=0)
    resource: Literal["stamina", "mp"] = "stamina"
    multiplier: float = Field(default=1, ge=0)
    status_effect: str | None = None
    status_chance: float | None = Field(default=None, ge=0, le=1)
    self_effect: str | None = None
    self_effect_ratio: float | None = Field(default=None, ge=0, le=1)


class RaceDefinition(BaseModel):
    id: str
    name: str
    ancestry: str
    nation: str
    background: str
    base_attributes: RaceAttributes
    strengths: list[str]
    weaknesses: list[str]
    traits: list[str] = Field(default_factory=list)
    exclusive_skills: list[RaceSkill] = Field(default_factory=list)
    birthplace: RaceBirthplace
    bonuses: dict[str, Any] = Field(default_factory=dict)
    conditional_modifiers: list[dict[str, Any]] = Field(default_factory=list)
    passives: dict[str, float] = Field(default_factory=dict)
    image_url: str | None = None

    @model_validator(mode="after")
    def validate_unique_skills(self) -> RaceDefinition:
        skill_ids = [skill.id for skill in self.exclusive_skills]
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError(f"Race {self.id} contains duplicate exclusive skill ids")
        return self


class RaceCatalogDocument(BaseModel):
    schema_version: str
    races: dict[str, RaceDefinition]

    @model_validator(mode="after")
    def validate_keys(self) -> RaceCatalogDocument:
        for race_id, definition in self.races.items():
            if definition.id != race_id:
                raise ValueError(f"Race key {race_id} does not match id {definition.id}")
        return self
