from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MapScale(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    CUSTOM = "custom"


class MapTemplate(BaseModel):
    template_id: str
    world_id: str
    region_id: str
    scale: MapScale
    width: int | None = Field(default=None, ge=1, le=200)
    height: int | None = Field(default=None, ge=1, le=200)
    terrain_weights: dict[str, float]
    landmarks: dict[int, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_custom_size(self) -> "MapTemplate":
        if self.scale is MapScale.CUSTOM and (self.width is None or self.height is None):
            raise ValueError("Custom map templates require width and height")
        if not self.terrain_weights or sum(self.terrain_weights.values()) <= 0:
            raise ValueError("Map templates require positive terrain weights")
        return self


class MapCell(BaseModel):
    cell_id: int
    x: int
    y: int
    terrain_id: str
    landmark_id: str | None = None
    passable: bool = True
    explored: bool = False
    gathered: bool = False


class MapInstance(BaseModel):
    map_id: str
    template_id: str
    world_id: str
    region_id: str
    scale: MapScale
    width: int
    height: int
    seed: int
    config_version: str
    current_cell_id: int = 0
    cells: list[MapCell]


class EncounterLocation(BaseModel):
    region_ids: list[str] = Field(default_factory=list)
    map_template_ids: list[str] = Field(default_factory=list)
    terrain_ids: list[str] = Field(default_factory=list)
    landmark_ids: list[str] = Field(default_factory=list)
    cell_ids: list[int] = Field(default_factory=list)


class EncounterCondition(BaseModel):
    kind: Literal[
        "relationship_at_least",
        "relationship_at_most",
        "relationship_flag",
        "memory_tag",
    ]
    field: Literal["affinity", "trust", "respect", "hostility"] | None = None
    threshold: int | None = None
    values: list[str] = Field(default_factory=list)


class EncounterRule(BaseModel):
    encounter_id: str
    npc_id: str
    locations: EncounterLocation
    trigger: Literal["on_enter_map", "on_enter_cell", "on_gather"]
    chance: float = Field(ge=0, le=1)
    priority: int = 0
    conditions: list[EncounterCondition] = Field(default_factory=list)
    cooldown_seconds: int = Field(default=0, ge=0)
    repeatable: bool = False
    story_hook_id: str | None = None


class EncounterResult(BaseModel):
    encounter_id: str
    npc_id: str
    story_hook_id: str | None = None
    trigger: str
