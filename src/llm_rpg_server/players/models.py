from __future__ import annotations

from datetime import datetime
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


class PersistentCombatStatus(BaseModel):
    status_id: str
    name: str
    source_id: str = "environment"
    stacks: int = Field(default=1, ge=1)
    potency: float = Field(default=1, ge=0)
    remaining_turns: int = Field(default=1, ge=0)
    persistent: bool = True
    tags: list[str] = Field(default_factory=list)


class CharacterAttributes(BaseModel):
    vitality: int = Field(default=5, ge=1)
    strength: int = Field(default=5, ge=1)
    agility: int = Field(default=5, ge=1)
    wisdom: int = Field(default=5, ge=1)
    luck: int = Field(default=5, ge=1)


class QuestProgress(BaseModel):
    hook_id: str
    npc_id: str
    title: str
    summary: str
    xp_reward: int = Field(ge=0)
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    related_npc_ids: list[str] = Field(default_factory=list)
    status: Literal["active", "completed"] = "active"
    started_game_hour: int | None = None
    completed_game_hour: int | None = None


class SleepState(BaseModel):
    started_at: datetime
    started_game_hour: int = Field(ge=0)
    duration_seconds: int = Field(default=60, gt=0)
    duration_game_hours: int = Field(default=6, gt=0)
    start_hp: int = Field(ge=0)
    start_mp: int = Field(ge=0)
    start_stamina: int = Field(ge=0)
    location_kind: Literal["camp", "inn"]


class PlayerProfile(BaseModel):
    player_id: str
    name: str
    character_id: str
    race_id: str = "1"
    level: int = Field(default=1, ge=1)
    experience: int = Field(default=0, ge=0)
    experience_to_next: int = Field(default=100, gt=0)
    total_experience: int = Field(default=0, ge=0)
    attribute_points: int = Field(default=0, ge=0)
    attributes: CharacterAttributes = Field(default_factory=CharacterAttributes)
    active_quests: dict[str, QuestProgress] = Field(default_factory=dict)
    completed_quests: list[str] = Field(default_factory=list)
    quest_history: dict[str, QuestProgress] = Field(default_factory=dict)
    gold: int = Field(ge=0)
    inventory: Inventory
    current_hp: int = Field(default=1, ge=0)
    max_hp: int = Field(default=1, gt=0)
    current_mp: int = Field(default=0, ge=0)
    max_mp: int = Field(default=0, ge=0)
    stamina: int = Field(default=100, ge=0)
    max_stamina: int = Field(default=100, gt=0)
    combat_statuses: list[PersistentCombatStatus] = Field(default_factory=list)
    psychological_traits: list[str] = Field(default_factory=list)
    equipped_weapon_id: str | None = None
    equipped_armor_id: str | None = None
    equipped_item_id: str | None = None
    world_seed: int | None = None
    current_map: dict[str, Any] | None = None
    world_maps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    last_camped_game_day: int | None = None
    last_stamina_recovery_game_hour: int | None = None
    sleep: SleepState | None = None
    encountered_npc_ids: list[str] = Field(default_factory=list)
    world_event_states: dict[str, WorldEventState] = Field(default_factory=dict)
    world_event_log: list[WorldEventLogEntry] = Field(default_factory=list)
