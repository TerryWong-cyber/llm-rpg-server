"""Schema contracts for the world/NPC subsystem.

These models deliberately contain no FastAPI or LangChain code.  The same
contracts can therefore be stored in a database, fed to an LLM, or consumed by
combat and quest systems without creating imports between those systems.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class NPCLocation(BaseModel):
    region: str
    terrain_id: Optional[str] = None
    landmark: str = ""
    cell_ids: List[int] = Field(default_factory=list)


class NPCEquipment(BaseModel):
    weapon_id: Optional[str] = None
    armor_id: Optional[str] = None
    items: Dict[str, int] = Field(default_factory=dict)
    valuables: List[str] = Field(default_factory=list)


class NPCCombatProfile(BaseModel):
    """Only IDs and fixed numbers are accepted here; combat rules stay in code."""

    character_id: str
    weapon_id: str
    armor_id: str
    item_id: Optional[str] = None
    item_count: int = Field(default=0, ge=0)
    threat: int = Field(default=1, ge=1, le=10)
    tactics: List[str] = Field(default_factory=list)
    arena: str = "荒野遭遇战"


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
    """A small, data-driven condition language for dialogue and combat triggers."""

    kind: Literal[
        "message_contains",
        "relationship_at_least",
        "relationship_at_most",
        "memory_tag",
        "relationship_flag",
    ]
    field: Optional[Literal["affinity", "trust", "respect", "hostility"]] = None
    values: List[str] = Field(default_factory=list)
    threshold: Optional[int] = None


class NPCCombatTrigger(BaseModel):
    trigger_id: str
    title: str
    intro: str
    conditions: List[TriggerCondition] = Field(default_factory=list)
    repeatable: bool = False


class StoryHook(BaseModel):
    hook_id: str
    title: str
    summary: str
    min_affinity: int = -100
    min_trust: int = -100
    requires_memory_tags: List[str] = Field(default_factory=list)


class NPCProfile(BaseModel):
    npc_id: str
    name: str
    title: str
    gender: str
    race: str
    appearance: str
    location: NPCLocation
    personality: List[str]
    conversation_style: str
    backstory: NPCBackstory
    equipment: NPCEquipment = Field(default_factory=NPCEquipment)
    combat: Optional[NPCCombatProfile] = None
    initial_disposition: NPCDisposition = Field(default_factory=NPCDisposition)
    story_hooks: List[StoryHook] = Field(default_factory=list)
    combat_triggers: List[NPCCombatTrigger] = Field(default_factory=list)


class NPCRelationship(BaseModel):
    npc_id: str
    player_id: str
    affinity: int = 0
    trust: int = 0
    respect: int = 0
    hostility: int = 0
    flags: List[str] = Field(default_factory=list)
    active_story_hooks: List[str] = Field(default_factory=list)
    armed_combat_triggers: List[str] = Field(default_factory=list)
    consumed_combat_triggers: List[str] = Field(default_factory=list)
    interaction_count: int = 0


class MemoryEntry(BaseModel):
    memory_id: str = Field(default_factory=lambda: uuid4().hex)
    owner_type: Literal["player", "npc", "world"]
    owner_id: str
    counterpart_id: Optional[str] = None
    summary: str
    tags: List[str] = Field(default_factory=list)
    importance: int = Field(default=2, ge=1, le=5)
    facts: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GeneratedDialogue(BaseModel):
    reply: str
    tone: str = "平静"
    mentioned_hook_id: Optional[str] = None
    memory_summary: str = ""

