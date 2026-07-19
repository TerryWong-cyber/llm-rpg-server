from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


SkillContext = Literal["combat", "exploration", "world_event"]
SkillType = Literal["damage", "defense", "healing", "buff", "control", "utility", "passive"]


class SkillConditions(BaseModel):
    race_ids: list[str] = Field(default_factory=list)
    minimum_level: int = Field(default=1, ge=1)
    minimum_attributes: dict[str, int] = Field(default_factory=dict)
    prerequisite_skill_ids: list[str] = Field(default_factory=list)
    required_weapon_types: list[str] = Field(default_factory=list)
    required_states: list[str] = Field(default_factory=list)


class SkillEffect(BaseModel):
    kind: Literal[
        "damage",
        "heal",
        "shield",
        "apply_status",
        "clear_status",
        "grant_exploration_state",
        "event_capability",
    ]
    target: Literal["self", "enemy", "all_enemies", "event"] = "enemy"
    damage_type: Literal["physical", "magical", "true", "elemental"] | None = None
    element: str | None = None
    fixed_amount: float = Field(default=0, ge=0)
    attribute_scaling: dict[str, float] = Field(default_factory=dict)
    weapon_multiplier: float = Field(default=0, ge=0)
    status_id: str | None = None
    chance: float = Field(default=1, ge=0, le=1)
    duration_turns: int | None = Field(default=None, ge=1)
    state_id: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1)
    capability: str | None = None


class SkillDefinition(BaseModel):
    id: str
    name: str
    description: str
    type: SkillType
    icon_url: str
    rarity: Literal["common", "uncommon", "rare", "epic", "legendary"] = "common"
    active: bool = True
    use_contexts: list[SkillContext] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    costs: dict[Literal["hp", "mp", "stamina"], int] = Field(default_factory=dict)
    effect_range: Literal["self", "single", "area", "all_enemies", "event"] = "single"
    cast_conditions: SkillConditions = Field(default_factory=SkillConditions)
    learning_conditions: SkillConditions = Field(default_factory=SkillConditions)
    effects: list[SkillEffect] = Field(default_factory=list)


class SkillUnlockRule(BaseModel):
    skill_id: str
    source: Literal["starter", "race_level"]
    race_ids: list[str] = Field(default_factory=list)
    level: int = Field(default=1, ge=1)


class TrainerOffer(BaseModel):
    skill_id: str
    gold_cost: int = Field(default=0, ge=0)
    relationship: str | None = None
    minimum_relationship: int = Field(default=0, ge=-100, le=100)


class SkillRulesDocument(BaseModel):
    schema_version: str
    max_combat_slots: int = Field(default=5, ge=1)
    skills: dict[str, SkillDefinition]
    unlocks: list[SkillUnlockRule] = Field(default_factory=list)
    trainers: dict[str, list[TrainerOffer]] = Field(default_factory=dict)
    world_events: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "SkillRulesDocument":
        for skill_id, skill in self.skills.items():
            if skill.id != skill_id:
                raise ValueError(f"Skill key {skill_id} does not match id {skill.id}")
        for unlock in self.unlocks:
            if unlock.skill_id not in self.skills:
                raise ValueError(f"Unlock references unknown skill {unlock.skill_id}")
        for npc_id, offers in self.trainers.items():
            if len({offer.skill_id for offer in offers}) != len(offers):
                raise ValueError(f"Trainer {npc_id} contains duplicate offers")
            for offer in offers:
                if offer.skill_id not in self.skills:
                    raise ValueError(f"Trainer {npc_id} references unknown skill {offer.skill_id}")
        return self
