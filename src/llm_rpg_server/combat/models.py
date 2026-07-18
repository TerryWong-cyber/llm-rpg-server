from typing import Any, Literal

from pydantic import BaseModel, Field


class CombatNarration(BaseModel):
    combat_narration: str


class EffectivenessAssessment(BaseModel):
    score: int = Field(default=5, ge=0, le=10)
    reason: str = "常规交锋，未出现显著的环境或心理优势。"
    factors: list[str] = Field(default_factory=list)


class CombatEffectivenessJudgement(BaseModel):
    player: EffectivenessAssessment = Field(default_factory=EffectivenessAssessment)
    opponent: EffectivenessAssessment = Field(default_factory=EffectivenessAssessment)


class DerivedStats(BaseModel):
    vitality: float = 0
    strength: float = 0
    agility: float = 0
    wisdom: float = 0
    luck: float = 0
    physical_power: float = 0
    spell_power: float = 0
    accuracy: float = 0
    evasion: float = 0
    critical_chance: float = 0
    critical_multiplier: float = 1.5
    physical_resistance: float = 0
    magic_resistance: float = 0
    elemental_resistances: dict[str, float] = Field(default_factory=dict)
    physical_penetration: float = 0
    magic_penetration: float = 0
    status_resistance: float = 0
    max_hp: int = 1
    max_mp: int = 0
    max_stamina: int = 1


class DamagePacket(BaseModel):
    physical: float = 0
    magical: float = 0
    elemental: dict[str, float] = Field(default_factory=dict)
    true: float = 0

    def scaled(self, coefficient: float) -> "DamagePacket":
        return DamagePacket(
            physical=self.physical * coefficient,
            magical=self.magical * coefficient,
            elemental={key: value * coefficient for key, value in self.elemental.items()},
            true=self.true * coefficient,
        )


class DamageBreakdown(BaseModel):
    hit: bool = True
    critical: bool = False
    effectiveness: int = 5
    hit_chance: float = 1
    physical: int = 0
    magical: int = 0
    elemental: dict[str, int] = Field(default_factory=dict)
    true: int = 0
    total: int = 0


class StatusInstance(BaseModel):
    status_id: str
    name: str
    source_id: str = "environment"
    stacks: int = Field(default=1, ge=1)
    potency: float = Field(default=1, ge=0)
    remaining_turns: int = Field(default=1, ge=0)
    persistent: bool = False
    tags: list[str] = Field(default_factory=list)


class ActionOutcome(BaseModel):
    hp_restore: int = 0
    mp_delta: int = 0
    stamina_delta: int = 0
    damage: DamageBreakdown = Field(default_factory=DamageBreakdown)
    statuses: list[StatusInstance] = Field(default_factory=list)
    clear_negative_statuses: bool = False
    audit: dict[str, Any] = Field(default_factory=dict)


class AttributeRules(BaseModel):
    hp_per_strength: float = Field(ge=0)
    hp_per_vitality: float = Field(ge=0)
    mp_per_wisdom: float = Field(ge=0)
    base_stamina: float = Field(gt=0)
    stamina_per_strength: float = Field(ge=0)
    stamina_per_agility: float = Field(ge=0)
    physical_power_per_strength: float = Field(ge=0)
    spell_power_per_wisdom: float = Field(ge=0)
    base_accuracy: float
    accuracy_per_agility: float = Field(ge=0)
    accuracy_per_luck: float = Field(ge=0)
    evasion_per_agility: float = Field(ge=0)
    base_critical_chance: float = Field(ge=0, le=1)
    critical_chance_per_luck: float = Field(ge=0)
    critical_chance_per_agility: float = Field(ge=0)
    critical_chance_cap: float = Field(ge=0, le=1)
    critical_multiplier: float = Field(ge=1)
    physical_resistance_per_strength: float = Field(ge=0)
    magic_resistance_per_wisdom: float = Field(ge=0)
    status_resistance_per_wisdom: float = Field(ge=0)
    status_resistance_per_luck: float = Field(ge=0)
    status_resistance_per_vitality: float = Field(ge=0)


class DamageRules(BaseModel):
    min_hit_chance: float = Field(ge=0, le=1)
    max_hit_chance: float = Field(ge=0, le=1)


class StatusTickDefinition(BaseModel):
    kind: Literal["physical", "magical", "elemental", "true"]
    amount: float = Field(ge=0)
    element: str | None = None


class StatusDefinition(BaseModel):
    name: str
    base_chance: float = Field(ge=0, le=1)
    duration: int = Field(gt=0)
    potency: float = Field(default=1, ge=0)
    max_stacks: int = Field(default=1, ge=1)
    persistent: bool = False
    tags: list[str] = Field(default_factory=list)
    tick_damage: StatusTickDefinition | None = None
    attribute_multipliers: dict[str, float] = Field(default_factory=dict)
    environment_chance: dict[str, float] = Field(default_factory=dict)


class FallRules(BaseModel):
    min_mass_kg: float = Field(gt=0)
    max_mass_kg: float = Field(gt=0)
    max_height_m: float = Field(gt=0)
    energy_scale: float = Field(gt=0)


class DrowningRules(BaseModel):
    grace_turns: int = Field(ge=0)
    base_max_hp_ratio: float = Field(ge=0, le=1)
    growth_per_turn: float = Field(ge=0, le=1)
    max_hp_ratio_cap: float = Field(ge=0, le=1)


class EnvironmentHazardDefinition(BaseModel):
    id: str
    kind: Literal["high_temperature", "toxic_gas", "drowning"]
    severity: float = Field(default=1, ge=0)
    grace_turns: int | None = Field(default=None, ge=0)


class EnvironmentProfileDefinition(BaseModel):
    keywords: list[str]
    tags: list[str] = Field(default_factory=list)
    hazards: list[EnvironmentHazardDefinition] = Field(default_factory=list)


class EnvironmentRules(BaseModel):
    fall: FallRules
    drowning: DrowningRules
    profiles: list[EnvironmentProfileDefinition] = Field(default_factory=list)


class CombatRulesDocument(BaseModel):
    schema_version: str
    attributes: AttributeRules
    damage: DamageRules
    status_aliases: dict[str, str] = Field(default_factory=dict)
    statuses: dict[str, StatusDefinition]
    environment: EnvironmentRules
