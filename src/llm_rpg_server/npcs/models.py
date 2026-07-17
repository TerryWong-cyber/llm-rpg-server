from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class NPCLocation(BaseModel):
    region: str
    terrain_id: str | None = None
    landmark: str = ""
    cell_ids: list[int] = Field(default_factory=list)


class NPCEquipment(BaseModel):
    weapon_id: str | None = None
    armor_id: str | None = None
    items: dict[str, int] = Field(default_factory=dict)
    valuables: list[str] = Field(default_factory=list)


class NPCCombatProfile(BaseModel):
    character_id: str
    weapon_id: str
    armor_id: str
    item_id: str | None = None
    item_count: int = Field(default=0, ge=0)
    threat: int = Field(default=1, ge=1, le=10)
    tactics: list[str] = Field(default_factory=list)
    arena: str


class NPCBackstory(BaseModel):
    public_summary: str
    personal_goal: str
    private_secret: str = ""


class NPCDisposition(BaseModel):
    affinity: int = Field(default=0, ge=-100, le=100)
    trust: int = Field(default=0, ge=-100, le=100)
    respect: int = Field(default=0, ge=-100, le=100)
    hostility: int = Field(default=0, ge=0, le=100)


class TriggerCondition(BaseModel):
    kind: Literal[
        "message_contains",
        "relationship_at_least",
        "relationship_at_most",
        "memory_tag",
        "relationship_flag",
    ]
    field: Literal["affinity", "trust", "respect", "hostility"] | None = None
    values: list[str] = Field(default_factory=list)
    threshold: int | None = None


class NPCCombatTrigger(BaseModel):
    trigger_id: str
    title: str
    intro: str
    conditions: list[TriggerCondition] = Field(default_factory=list)
    repeatable: bool = False


class StoryHook(BaseModel):
    hook_id: str
    title: str
    summary: str
    min_affinity: int = -100
    min_trust: int = -100
    requires_memory_tags: list[str] = Field(default_factory=list)


class NPCProfile(BaseModel):
    npc_id: str
    name: str
    title: str
    gender: str
    race: str
    appearance: str
    image_url: str | None = None
    location: NPCLocation
    personality: list[str]
    conversation_style: str
    backstory: NPCBackstory
    equipment: NPCEquipment = Field(default_factory=NPCEquipment)
    combat: NPCCombatProfile | None = None
    initial_disposition: NPCDisposition = Field(default_factory=NPCDisposition)
    story_hooks: list[StoryHook] = Field(default_factory=list)
    combat_triggers: list[NPCCombatTrigger] = Field(default_factory=list)


class NPCRelationship(BaseModel):
    npc_id: str
    player_id: str
    affinity: int = 0
    trust: int = 0
    respect: int = 0
    hostility: int = 0
    flags: list[str] = Field(default_factory=list)
    active_story_hooks: list[str] = Field(default_factory=list)
    armed_combat_triggers: list[str] = Field(default_factory=list)
    consumed_combat_triggers: list[str] = Field(default_factory=list)
    interaction_count: int = 0


class MemoryEntry(BaseModel):
    memory_id: str = Field(default_factory=lambda: uuid4().hex)
    owner_type: Literal["player", "npc", "world"]
    owner_id: str
    counterpart_id: str | None = None
    summary: str
    tags: list[str] = Field(default_factory=list)
    importance: int = Field(default=2, ge=1, le=5)
    facts: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GeneratedDialogue(BaseModel):
    reply: str
    tone: str
    mentioned_hook_id: str | None = None
    memory_summary: str = ""
