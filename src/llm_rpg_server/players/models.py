from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Inventory(BaseModel):
    weapons: list[str] = Field(default_factory=list)
    armors: list[str] = Field(default_factory=list)
    items: dict[str, int] = Field(default_factory=dict)
    materials: dict[str, int] = Field(default_factory=dict)


class WorldEventState(BaseModel):
    event_id: str
    scope_key: str
    trigger_count: int = Field(default=0, ge=0)
    last_checked_game_day: int | None = None
    active: bool = False
    active_world_id: str | None = None
    active_region_id: str | None = None
    active_map_id: str | None = None
    active_cell_id: int | None = None
    active_since_game_day: int | None = None
    ended_game_day: int | None = None
    last_action_id: str | None = None


class WorldEventLogEntry(BaseModel):
    log_id: str
    event_id: str
    phase: Literal["triggered", "action", "expired"]
    title: str
    description: str
    emoji: str = "✦"
    kind: str = "flavor"
    world_id: str
    region_id: str
    map_id: str
    cell_id: int
    game_hour: int = Field(ge=0)
    year: int = Field(ge=0)
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1)
    hour: int = Field(ge=0, le=23)
    season: str


class PlayerProfile(BaseModel):
    player_id: str
    name: str
    character_id: str
    gold: int = Field(ge=0)
    inventory: Inventory
    stamina: int = Field(default=100, ge=0)
    max_stamina: int = Field(default=100, gt=0)
    world_seed: int | None = None
    current_map: dict[str, Any] | None = None
    world_maps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    last_camped_game_day: int | None = None
    world_event_states: dict[str, WorldEventState] = Field(default_factory=dict)
    world_event_log: list[WorldEventLogEntry] = Field(default_factory=list)
