from __future__ import annotations

import math
import random
from copy import deepcopy
from typing import Any

from llm_rpg_server.shared.config import ContentProvider

from .models import CombatRulesDocument, DamageBreakdown, DamagePacket, DerivedStats, StatusInstance


class CombatRulebook:
    """Pure, deterministic combat math driven by validated content."""

    def __init__(self, content: ContentProvider):
        definition = CombatRulesDocument.model_validate(content.document("combat/rules.json"))
        self.config = definition.model_dump(mode="json", exclude_none=True)
        self.attributes = self.config["attributes"]
        self.damage = self.config["damage"]
        self.statuses = self.config["statuses"]
        self.status_aliases = self.config.get("status_aliases", {})
        self.environment = self.config["environment"]

    def maximum_resources(
        self,
        character: dict[str, Any],
        armor: dict[str, Any] | None = None,
    ) -> tuple[int, int, int]:
        armor = armor or {}
        vitality = float(character.get("vit", character.get("vitality", 0)))
        strength = float(character.get("str", 0))
        agility = float(character.get("agi", 0))
        wisdom = float(character.get("wis", character.get("int", 0)))
        max_hp = int(
            character.get("hp", 1)
            + strength * self.attributes["hp_per_strength"]
            + vitality * self.attributes["hp_per_vitality"]
        )
        max_hp += int(armor.get("hp_bonus", 0))
        max_mp = int(character.get("mp", 0) + wisdom * self.attributes["mp_per_wisdom"])
        max_stamina = int(
            self.attributes["base_stamina"]
            + strength * self.attributes["stamina_per_strength"]
            + agility * self.attributes["stamina_per_agility"]
        )
        return max(1, max_hp), max(0, max_mp), max(1, max_stamina)

    def derive_stats(
        self,
        character: dict[str, Any],
        weapon: dict[str, Any],
        armor: dict[str, Any],
        statuses: list[dict[str, Any]] | None = None,
        environment_tags: list[str] | None = None,
    ) -> DerivedStats:
        vitality = float(character.get("vit", character.get("vitality", 0)))
        strength = float(character.get("str", 0))
        agility = float(character.get("agi", 0))
        wisdom = float(character.get("wis", character.get("int", 0)))
        luck = float(character.get("luck", 5))
        modifiers = {
            "vitality": 1.0,
            "strength": 1.0,
            "agility": 1.0,
            "wisdom": 1.0,
            "luck": 1.0,
        }
        for item in statuses or []:
            definition = self.statuses.get(item.get("status_id", ""), {})
            stacks = max(1, int(item.get("stacks", 1)))
            for key, value in definition.get("attribute_multipliers", {}).items():
                if key in modifiers:
                    modifiers[key] *= max(0.1, float(value) ** stacks)
        bonuses = deepcopy(character.get("bonuses", {}))
        active_tags = set(environment_tags or [])
        for conditional in character.get("conditional_modifiers", []):
            required = set(conditional.get("tags_any", []))
            if required and not required.intersection(active_tags):
                continue
            for key, value in conditional.get("attribute_multipliers", {}).items():
                if key in modifiers:
                    modifiers[key] *= max(0.1, float(value))
            for key, value in conditional.get("stat_bonuses", {}).items():
                if key == "elemental_resistances" and isinstance(value, dict):
                    elemental_bonus = bonuses.setdefault("elemental_resistances", {})
                    for element, amount in value.items():
                        elemental_bonus[element] = float(elemental_bonus.get(element, 0)) + float(amount)
                else:
                    bonuses[key] = float(bonuses.get(key, 0)) + float(value)
        vitality *= modifiers["vitality"]
        strength *= modifiers["strength"]
        agility *= modifiers["agility"]
        wisdom *= modifiers["wisdom"]
        luck *= modifiers["luck"]
        max_hp, max_mp, max_stamina = self.maximum_resources(character, armor)
        elemental = {
            key: float(value)
            for key, value in armor.get("elemental_resistances", {}).items()
        }
        for key, value in bonuses.get("elemental_resistances", {}).items():
            elemental[key] = elemental.get(key, 0) + float(value)
        critical_chance = min(
            float(self.attributes["critical_chance_cap"]),
            float(self.attributes["base_critical_chance"])
            + luck * float(self.attributes["critical_chance_per_luck"])
            + agility * float(self.attributes["critical_chance_per_agility"]),
        )
        return DerivedStats(
            vitality=vitality,
            strength=strength,
            agility=agility,
            wisdom=wisdom,
            luck=luck,
            physical_power=strength * float(self.attributes["physical_power_per_strength"])
            + float(bonuses.get("physical_power", 0)),
            spell_power=wisdom * float(self.attributes["spell_power_per_wisdom"])
            + float(bonuses.get("spell_power", 0)),
            accuracy=float(self.attributes["base_accuracy"])
            + agility * float(self.attributes["accuracy_per_agility"])
            + luck * float(self.attributes["accuracy_per_luck"])
            + float(weapon.get("accuracy_bonus", 0))
            + float(bonuses.get("accuracy", 0)),
            evasion=agility * float(self.attributes["evasion_per_agility"])
            + float(armor.get("evasion_bonus", 0))
            + float(bonuses.get("evasion", 0)),
            critical_chance=critical_chance,
            critical_multiplier=float(self.attributes["critical_multiplier"]),
            physical_resistance=float(armor.get("physical_resistance", 0))
            + strength * float(self.attributes["physical_resistance_per_strength"])
            + float(bonuses.get("physical_resistance", 0)),
            magic_resistance=float(armor.get("magic_resistance", 0))
            + wisdom * float(self.attributes["magic_resistance_per_wisdom"])
            + float(bonuses.get("magic_resistance", 0)),
            elemental_resistances=elemental,
            physical_penetration=float(weapon.get("physical_penetration", 0)),
            magic_penetration=float(weapon.get("magic_penetration", 0)),
            status_resistance=wisdom * float(self.attributes["status_resistance_per_wisdom"])
            + luck * float(self.attributes["status_resistance_per_luck"])
            + vitality * float(self.attributes["status_resistance_per_vitality"])
            + float(armor.get("status_resistance", 0))
            + float(bonuses.get("status_resistance", 0)),
            max_hp=max_hp,
            max_mp=max_mp,
            max_stamina=max_stamina,
        )

    def attack_packet(
        self,
        weapon: dict[str, Any],
        stats: DerivedStats,
        multiplier: float = 1,
    ) -> DamagePacket:
        base_damage = float(weapon.get("base_dmg", 0))
        elemental = deepcopy(weapon.get("elemental_damage", {}))
        physical = float(weapon.get("physical_damage", base_damage if weapon.get("type") == "phys" else 0))
        magical = float(weapon.get("magic_damage", base_damage if weapon.get("type") == "magic" else 0))
        if weapon.get("type") == "phys":
            physical += stats.physical_power
        else:
            if elemental:
                primary = max(elemental, key=elemental.get)
                elemental[primary] = float(elemental[primary]) + stats.spell_power
            else:
                magical += stats.spell_power
        return DamagePacket(
            physical=physical * multiplier,
            magical=magical * multiplier,
            elemental={key: float(value) * multiplier for key, value in elemental.items()},
            true=float(weapon.get("true_damage", 0)) * multiplier,
        )

    def resolve_damage(
        self,
        packet: DamagePacket,
        attacker: DerivedStats,
        target: DerivedStats,
        effectiveness: int,
        rng: random.Random,
        *,
        can_miss: bool = True,
        can_crit: bool = True,
    ) -> DamageBreakdown:
        score = max(0, min(10, int(effectiveness)))
        hit_chance = self._hit_chance(attacker, target) if can_miss else 1.0
        hit = score > 0 and (not can_miss or rng.random() <= hit_chance)
        if not hit:
            return DamageBreakdown(hit=False, effectiveness=score, hit_chance=hit_chance)
        coefficient = score / 5.0
        critical = can_crit and rng.random() <= attacker.critical_chance
        crit_multiplier = attacker.critical_multiplier if critical else 1.0
        scaled = DamagePacket(
            physical=packet.physical * coefficient * crit_multiplier,
            magical=packet.magical * coefficient * crit_multiplier,
            elemental={key: value * coefficient * crit_multiplier for key, value in packet.elemental.items()},
            true=packet.true * coefficient,
        )
        physical = self._mitigate(
            scaled.physical,
            target.physical_resistance - attacker.physical_penetration,
        )
        magical = self._mitigate(
            scaled.magical,
            target.magic_resistance - attacker.magic_penetration,
        )
        elemental = {
            key: self._mitigate(
                value,
                target.magic_resistance
                + target.elemental_resistances.get(key, 0)
                - attacker.magic_penetration,
            )
            for key, value in scaled.elemental.items()
        }
        result = DamageBreakdown(
            hit=True,
            critical=critical,
            effectiveness=score,
            hit_chance=hit_chance,
            physical=max(0, round(physical)),
            magical=max(0, round(magical)),
            elemental={key: max(0, round(value)) for key, value in elemental.items()},
            true=max(0, round(scaled.true)),
        )
        result.total = result.physical + result.magical + result.true + sum(result.elemental.values())
        return result

    def apply_status(
        self,
        current: list[dict[str, Any]],
        status: str | None,
        source_id: str,
        target: DerivedStats,
        rng: random.Random,
        environment_tags: list[str] | None = None,
        base_chance: float | None = None,
    ) -> list[dict[str, Any]]:
        if not status:
            return current
        status_id = self.status_aliases.get(status, status)
        definition = self.statuses.get(status_id)
        if definition is None:
            return current
        resistance_factor = 100 / (100 + max(0, target.status_resistance))
        chance = float(base_chance if base_chance is not None else definition["base_chance"])
        for tag in environment_tags or []:
            chance *= float(definition.get("environment_chance", {}).get(tag, 1))
        if rng.random() > min(1.0, max(0.0, chance * resistance_factor)):
            return current
        duration = max(1, round(float(definition["duration"]) * resistance_factor))
        instance = StatusInstance(
            status_id=status_id,
            name=definition["name"],
            source_id=source_id,
            stacks=1,
            potency=float(definition.get("potency", 1)),
            remaining_turns=duration,
            persistent=bool(definition.get("persistent", False)),
            tags=list(definition.get("tags", [])),
        )
        updated = [dict(item) for item in current]
        existing = next((item for item in updated if item.get("status_id") == status_id), None)
        if existing is None:
            updated.append(instance.model_dump(mode="json"))
        else:
            existing["stacks"] = min(
                int(definition.get("max_stacks", 1)),
                int(existing.get("stacks", 1)) + 1,
            )
            existing["remaining_turns"] = max(int(existing.get("remaining_turns", 0)), duration)
            existing["potency"] = max(float(existing.get("potency", 1)), instance.potency)
        return updated

    def tick_statuses(
        self,
        current: list[dict[str, Any]],
        target: DerivedStats,
        rng: random.Random,
    ) -> tuple[list[dict[str, Any]], DamageBreakdown]:
        packet = DamagePacket()
        remaining: list[dict[str, Any]] = []
        for raw in current:
            item = StatusInstance.model_validate(raw)
            definition = self.statuses.get(item.status_id, {})
            tick = definition.get("tick_damage", {})
            amount = float(tick.get("amount", 0)) * item.potency * item.stacks
            kind = tick.get("kind")
            if kind == "physical":
                packet.physical += amount
            elif kind == "magical":
                packet.magical += amount
            elif kind == "true":
                packet.true += amount
            elif kind == "elemental" and tick.get("element"):
                element = str(tick["element"])
                packet.elemental[element] = packet.elemental.get(element, 0) + amount
            item.remaining_turns -= 1
            if item.remaining_turns > 0:
                remaining.append(item.model_dump(mode="json"))
        neutral = DerivedStats()
        damage = self.resolve_damage(
            packet,
            neutral,
            target,
            5,
            rng,
            can_miss=False,
            can_crit=False,
        )
        return remaining, damage

    def is_incapacitated(self, statuses: list[dict[str, Any]]) -> bool:
        return any("incapacitating" in item.get("tags", []) for item in statuses)

    def environment_context(self, description: str) -> dict[str, Any]:
        lowered = description.lower()
        tags: set[str] = set()
        hazards: list[dict[str, Any]] = []
        for profile in self.environment.get("profiles", []):
            if any(keyword.lower() in lowered for keyword in profile.get("keywords", [])):
                tags.update(profile.get("tags", []))
                hazards.extend(deepcopy(profile.get("hazards", [])))
        return {"description": description, "tags": sorted(tags), "hazards": hazards}

    def environment_tick(
        self,
        context: dict[str, Any],
        exposure: dict[str, int],
        target: DerivedStats,
        statuses: list[dict[str, Any]],
        rng: random.Random,
        source_id: str,
    ) -> tuple[dict[str, int], list[dict[str, Any]], DamageBreakdown]:
        packet = DamagePacket()
        updated_exposure = dict(exposure)
        updated_statuses = list(statuses)
        for hazard in context.get("hazards", []):
            hazard_id = str(hazard["id"])
            kind = hazard["kind"]
            severity = float(hazard.get("severity", 1))
            updated_exposure[hazard_id] = updated_exposure.get(hazard_id, 0) + 1
            if kind == "high_temperature":
                packet.elemental["fire"] = packet.elemental.get("fire", 0) + max(1, target.max_hp * 0.015 * severity)
                updated_statuses = self.apply_status(
                    updated_statuses, "burn", source_id, target, rng, context.get("tags"), 0.18 * severity
                )
            elif kind == "toxic_gas":
                updated_statuses = self.apply_status(
                    updated_statuses, "poison", source_id, target, rng, context.get("tags"), 0.25 * severity
                )
            elif kind == "drowning":
                grace = int(hazard.get("grace_turns", self.environment["drowning"]["grace_turns"]))
                if updated_exposure[hazard_id] > grace:
                    packet.true += self.drowning_damage(updated_exposure[hazard_id] - grace, target.max_hp)
        damage = self.resolve_damage(
            packet,
            DerivedStats(),
            target,
            5,
            rng,
            can_miss=False,
            can_crit=False,
        )
        return updated_exposure, updated_statuses, damage

    def fall_damage(self, height_m: float, mass_kg: float, ground_hardness: float, mitigation: float = 0) -> int:
        rules = self.environment["fall"]
        height = min(float(rules["max_height_m"]), max(0.0, height_m))
        mass = min(float(rules["max_mass_kg"]), max(float(rules["min_mass_kg"]), mass_kg))
        hardness = min(1.5, max(0.1, ground_hardness))
        energy = mass * 9.81 * height
        raw = math.sqrt(energy) * float(rules["energy_scale"]) * hardness
        return max(0, round(raw * (1 - min(0.9, max(0.0, mitigation)))))

    def drowning_damage(self, turns_without_air: int, max_hp: int) -> int:
        rules = self.environment["drowning"]
        coefficient = float(rules["base_max_hp_ratio"]) + max(0, turns_without_air - 1) * float(
            rules["growth_per_turn"]
        )
        return max(1, round(max_hp * min(float(rules["max_hp_ratio_cap"]), coefficient)))

    def _hit_chance(self, attacker: DerivedStats, target: DerivedStats) -> float:
        chance = (attacker.accuracy - target.evasion) / 100
        return min(float(self.damage["max_hit_chance"]), max(float(self.damage["min_hit_chance"]), chance))

    @staticmethod
    def _mitigate(amount: float, resistance: float) -> float:
        multiplier = 100 / (100 + resistance) if resistance >= 0 else 2 - 100 / (100 - resistance)
        return amount * multiplier
