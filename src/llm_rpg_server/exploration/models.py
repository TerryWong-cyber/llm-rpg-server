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
    terrain_weights: dict[str, float] = Field(default_factory=dict)
    terrain_counts: dict[str, int] = Field(default_factory=dict)
    primary_terrain_id: str | None = None
    landmarks: dict[int, str] = Field(default_factory=dict)
    landmark_terrains: dict[int, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_custom_size(self) -> "MapTemplate":
        if self.scale is MapScale.CUSTOM and (self.width is None or self.height is None):
            raise ValueError("Custom map templates require width and height")
        if not self.terrain_counts and (
            not self.terrain_weights or sum(self.terrain_weights.values()) <= 0
        ):
            raise ValueError("Map templates require terrain counts or positive terrain weights")
        if any(value < 0 for value in self.terrain_counts.values()):
            raise ValueError("Terrain counts cannot be negative")
        return self


class MapCell(BaseModel):
    cell_id: int
    x: int
    y: int
    terrain_id: str
    landmark_id: str | None = None
    passable: bool = True
    terrain_category: str = "ordinary"
    tags: list[str] = Field(default_factory=list)
    gatherable: bool = False
    campable: bool = False
    movement_cost: int = Field(default=1, ge=0)
    npc_chance_multiplier: float = Field(default=1.0, ge=0)
    interaction_ids: list[str] = Field(default_factory=list)
    explored: bool = False
    gathered: bool = False
    triggered_event_ids: list[str] = Field(default_factory=list)


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
    world_x: int = 0
    world_y: int = 0
    world_width: int = 1
    world_height: int = 1
    current_cell_id: int = 0
    cells: list[MapCell]


class MapTransition(BaseModel):
    from_region_id: str
    to_region_id: str
    direction: Literal["up", "down", "left", "right"]


class WorldEventAction(BaseModel):
    action_id: str
    label: str
    style: Literal["primary", "quiet", "danger"] = "quiet"
    kind: Literal[
        "narrative",
        "open_npc",
        "start_quest",
        "npc_combat",
        "monster_combat",
        "use_item",
        "use_skill",
    ] = "narrative"
    forced: bool = False
    eligible_items: list[dict] = Field(default_factory=list)
    eligible_skills: list[dict] = Field(default_factory=list)


class WorldEventResult(BaseModel):
    event_id: str
    kind: Literal["flavor", "discovery", "danger", "combat_hint", "settlement"]
    title: str
    description: str
    emoji: str = "✦"
    trigger: str
    state: Literal["triggered", "active", "expired", "action"] = "triggered"
    actions: list[WorldEventAction] = Field(default_factory=list)
    trigger_scope: Literal["cell", "region", "world"] = "cell"
    trigger_count: int = Field(default=1, ge=0)
    max_triggers: int | None = Field(default=None, ge=1)
    cell_id: int | None = None
    participant: dict | None = None
    blocks_movement: bool = False


class ActionAvailability(BaseModel):
    available: bool
    reason: str = ""
    cost: int = Field(default=0, ge=0)


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
        "time_period",
        "season",
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
