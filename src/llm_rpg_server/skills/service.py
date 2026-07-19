from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players.models import ExplorationSkillEffect, LearnedSkill, PlayerProfile
from llm_rpg_server.players.repository import PlayerRepository
from llm_rpg_server.shared.config import ContentProvider

from .catalog import SkillCatalog
from .models import SkillConditions, SkillDefinition

if TYPE_CHECKING:
    from llm_rpg_server.players.chronicle import CharacterChronicleService


class SkillService:
    """Authoritative acquisition, loadout, resource-cost, and timed exploration state rules."""

    def __init__(
        self,
        players: PlayerRepository,
        catalog: SkillCatalog,
        items: Catalog,
        content: ContentProvider,
    ):
        self.players = players
        self.catalog = catalog
        self.items = items
        self.content = content
        self.chronicle: CharacterChronicleService | None = None

    def set_chronicle(self, chronicle: CharacterChronicleService) -> None:
        self.chronicle = chronicle

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    def sync_unlocks(self, player_id: str) -> PlayerProfile:
        with self.players.transaction(player_id) as profile:
            self.apply_level_unlocks(profile)
        return self.players.get(player_id)

    def apply_level_unlocks(self, profile: PlayerProfile) -> list[str]:
        learned: list[str] = []
        for rule in self.catalog.rules.unlocks:
            if profile.level < rule.level:
                continue
            if rule.race_ids and profile.race_id not in rule.race_ids:
                continue
            if self._grant(profile, rule.skill_id, rule.source, f"level:{rule.level}"):
                learned.append(rule.skill_id)
        self._ensure_valid_loadout(profile)
        return learned

    def learn_from_book(self, player_id: str, item_id: str) -> tuple[PlayerProfile, str]:
        definition = self.items.item_definition("item", item_id)
        skill_id = str(definition.get("learn_skill_id", "")) if definition else ""
        if not skill_id:
            raise ValueError(self.content.text("errors.skill.not_book"))
        with self.players.transaction(player_id) as profile:
            if profile.inventory.items.get(item_id, 0) <= 0:
                raise ValueError(self.content.text("errors.inventory.not_owned"))
            if skill_id in profile.learned_skills:
                raise ValueError(self.content.text("errors.skill.already_learned_book"))
            skill = self.catalog.get(skill_id)
            self._require_learning_conditions(profile, skill)
            self._grant(profile, skill_id, "skill_book", item_id)
            profile.inventory.items[item_id] -= 1
            if profile.inventory.items[item_id] <= 0:
                profile.inventory.items.pop(item_id, None)
            self._ensure_valid_loadout(profile)
        return self.players.get(player_id), skill_id

    def trainer_view(self, player_id: str, npc_id: str) -> list[dict[str, Any]]:
        profile = self.players.get(player_id)
        self._require_trainer_access(profile, npc_id)
        result: list[dict[str, Any]] = []
        for offer in self.catalog.trainer_offers(npc_id):
            skill = self.catalog.get(offer.skill_id)
            reasons = self.learning_reasons(profile, skill)
            if profile.gold < offer.gold_cost:
                reasons.append(self.content.text("errors.skill.trainer_gold", gold=offer.gold_cost))
            result.append({
                "skill": skill.model_dump(mode="json"),
                "gold_cost": offer.gold_cost,
                "learned": offer.skill_id in profile.learned_skills,
                "available": offer.skill_id not in profile.learned_skills and not reasons,
                "unavailable_reasons": reasons,
            })
        return result

    def learn_from_trainer(self, player_id: str, npc_id: str, skill_id: str) -> PlayerProfile:
        offer = next((item for item in self.catalog.trainer_offers(npc_id) if item.skill_id == skill_id), None)
        if offer is None:
            raise ValueError(self.content.text("errors.skill.trainer_unknown"))
        with self.players.transaction(player_id) as profile:
            self._require_trainer_access(profile, npc_id)
            if skill_id in profile.learned_skills:
                raise ValueError(self.content.text("errors.skill.already_learned"))
            skill = self.catalog.get(skill_id)
            self._require_learning_conditions(profile, skill)
            if profile.gold < offer.gold_cost:
                raise ValueError(self.content.text("errors.shop.gold"))
            profile.gold -= offer.gold_cost
            self._grant(profile, skill_id, "npc", npc_id)
            self._ensure_valid_loadout(profile)
        return self.players.get(player_id)

    def grant_quest_rewards(self, profile: PlayerProfile, quest_id: str, skill_ids: list[str]) -> list[str]:
        return [
            skill_id for skill_id in skill_ids
            if self._grant(profile, skill_id, "quest", quest_id)
        ]

    def equip(self, player_id: str, skill_ids: list[str]) -> PlayerProfile:
        normalized = list(dict.fromkeys(map(str, skill_ids)))
        if len(normalized) != len(skill_ids):
            raise ValueError(self.content.text("errors.skill.duplicate_loadout"))
        if len(normalized) > self.catalog.max_combat_slots:
            raise ValueError(self.content.text("errors.skill.slot_limit", slots=self.catalog.max_combat_slots))
        with self.players.transaction(player_id) as profile:
            for skill_id in normalized:
                if skill_id not in profile.learned_skills:
                    raise ValueError(self.content.text("errors.skill.not_learned"))
                skill = self.catalog.get(skill_id)
                if not skill.active or "combat" not in skill.use_contexts:
                    raise ValueError(self.content.text("errors.skill.not_combat"))
            profile.equipped_skill_ids = normalized
        return self.players.get(player_id)

    def cast_exploration(self, player_id: str, skill_id: str) -> tuple[PlayerProfile, dict[str, Any]]:
        skill = self.catalog.get(skill_id)
        if "exploration" not in skill.use_contexts:
            raise ValueError(self.content.text("errors.skill.not_exploration"))
        with self.players.transaction(player_id) as profile:
            self._settle(profile)
            self._require_cast(profile, skill)
            before_hp = profile.current_hp
            self._pay_costs(profile, skill)
            applied = self._apply_exploration_effects(profile, skill)
            restored = profile.current_hp - before_hp
        return self.players.get(player_id), {
            "skill_id": skill_id,
            "applied_states": applied,
            "hp_restored": max(0, restored),
        }

    def event_options(self, player_id: str, requirements: dict[str, Any]) -> list[dict[str, Any]]:
        profile = self.players.get(player_id)
        self._settle(profile)
        skill_ids = set(map(str, requirements.get("skill_ids", [])))
        any_tags = set(map(str, requirements.get("any_tags", [])))
        options: list[dict[str, Any]] = []
        for learned_id in profile.learned_skills:
            skill = self.catalog.get(learned_id)
            if "world_event" not in skill.use_contexts:
                continue
            if skill_ids and learned_id not in skill_ids:
                continue
            if any_tags and not any_tags.intersection(skill.tags):
                continue
            reasons = self.cast_reasons(profile, skill)
            options.append({
                "skill": skill.model_dump(mode="json"),
                "available": not reasons,
                "unavailable_reasons": reasons,
            })
        return options

    def use_for_event(self, player_id: str, skill_id: str, requirements: dict[str, Any]) -> dict[str, Any]:
        eligible = {entry["skill"]["id"]: entry for entry in self.event_options(player_id, requirements)}
        option = eligible.get(skill_id)
        if option is None:
            raise ValueError(self.content.text("errors.skill.event_mismatch"))
        if not option["available"]:
            raise ValueError("；".join(option["unavailable_reasons"]))
        skill = self.catalog.get(skill_id)
        with self.players.transaction(player_id) as profile:
            self._settle(profile)
            self._require_cast(profile, skill)
            self._pay_costs(profile, skill)
            states = self._apply_exploration_effects(profile, skill)
        return {"skill_id": skill_id, "applied_states": states}

    def has_active_state(self, player_id: str, state_ids: list[str]) -> bool:
        with self.players.transaction(player_id) as profile:
            self._settle(profile)
            active = {effect.state_id for effect in profile.exploration_effects}
            return all(state_id in active for state_id in state_ids)

    def public_player_state(self, profile: PlayerProfile) -> dict[str, Any]:
        self._settle(profile)
        return {
            "learned_skills": {
                skill_id: record.model_dump(mode="json")
                for skill_id, record in profile.learned_skills.items()
            },
            "equipped_skill_ids": list(profile.equipped_skill_ids),
            "exploration_effects": [effect.model_dump(mode="json") for effect in profile.exploration_effects],
            "max_combat_skill_slots": self.catalog.max_combat_slots,
        }

    def combat_skills(self, profile: PlayerProfile) -> list[dict[str, Any]]:
        return [
            self.catalog.get(skill_id).model_dump(mode="json")
            for skill_id in profile.equipped_skill_ids
            if skill_id in profile.learned_skills
        ]

    def learning_reasons(self, profile: PlayerProfile, skill: SkillDefinition) -> list[str]:
        conditions = skill.learning_conditions
        reasons: list[str] = []
        if conditions.race_ids and profile.race_id not in conditions.race_ids:
            reasons.append(self.content.text("errors.skill.race_condition"))
        if profile.level < conditions.minimum_level:
            reasons.append(self.content.text("errors.skill.level_condition", level=conditions.minimum_level))
        for name, minimum in conditions.minimum_attributes.items():
            if getattr(profile.attributes, name, 0) < minimum:
                reasons.append(self.content.text("errors.skill.attribute_condition", attribute=name, minimum=minimum))
        missing = [skill_id for skill_id in conditions.prerequisite_skill_ids if skill_id not in profile.learned_skills]
        if missing:
            reasons.append(self.content.text("errors.skill.prerequisite_condition"))
        return reasons

    def cast_reasons(self, profile: PlayerProfile, skill: SkillDefinition) -> list[str]:
        reasons = self.learning_reasons_for_conditions(profile, skill.cast_conditions)
        if skill.id not in profile.learned_skills:
            reasons.append(self.content.text("errors.skill.not_learned_cast"))
        for resource, cost in skill.costs.items():
            current = self._resource_value(profile, resource)
            if current < cost or (resource == "hp" and current <= cost):
                reasons.append(self.content.text("errors.skill.resource_condition", resource=resource))
        if skill.cast_conditions.required_weapon_types:
            weapon = self.items.weapons.get(profile.equipped_weapon_id or "", {})
            if weapon.get("type") not in skill.cast_conditions.required_weapon_types:
                reasons.append(self.content.text("errors.skill.weapon_condition"))
        active_states = {effect.state_id for effect in profile.exploration_effects}
        if not set(skill.cast_conditions.required_states).issubset(active_states):
            reasons.append(self.content.text("errors.skill.state_condition"))
        return reasons

    def learning_reasons_for_conditions(self, profile: PlayerProfile, conditions: SkillConditions) -> list[str]:
        placeholder = SkillDefinition(
            id="condition", name="condition", description="condition", type="utility", icon_url="",
            learning_conditions=conditions,
        )
        return self.learning_reasons(profile, placeholder)

    def _require_learning_conditions(self, profile: PlayerProfile, skill: SkillDefinition) -> None:
        reasons = self.learning_reasons(profile, skill)
        if reasons:
            raise ValueError("；".join(reasons))

    def _require_trainer_access(self, profile: PlayerProfile, npc_id: str) -> None:
        if npc_id not in profile.encountered_npc_ids:
            raise ValueError(self.content.text("errors.skill.trainer_not_met"))

    def _require_cast(self, profile: PlayerProfile, skill: SkillDefinition) -> None:
        reasons = self.cast_reasons(profile, skill)
        if reasons:
            raise ValueError("；".join(reasons))

    def _pay_costs(self, profile: PlayerProfile, skill: SkillDefinition) -> None:
        profile.current_hp -= skill.costs.get("hp", 0)
        profile.current_mp -= skill.costs.get("mp", 0)
        profile.stamina -= skill.costs.get("stamina", 0)

    @staticmethod
    def _resource_value(profile: PlayerProfile, resource: str) -> int:
        return {
            "hp": profile.current_hp,
            "mp": profile.current_mp,
            "stamina": profile.stamina,
        }[resource]

    def _apply_exploration_effects(self, profile: PlayerProfile, skill: SkillDefinition) -> list[str]:
        now = self.now()
        applied: list[str] = []
        for effect in skill.effects:
            if effect.kind == "heal" and effect.target == "self":
                amount = effect.fixed_amount + sum(
                    getattr(profile.attributes, attribute, 0) * coefficient
                    for attribute, coefficient in effect.attribute_scaling.items()
                )
                profile.current_hp = min(profile.max_hp, profile.current_hp + int(amount))
                continue
            if effect.kind != "grant_exploration_state" or not effect.state_id or not effect.duration_seconds:
                continue
            profile.exploration_effects = [
                current for current in profile.exploration_effects if current.state_id != effect.state_id
            ]
            profile.exploration_effects.append(ExplorationSkillEffect(
                state_id=effect.state_id,
                name=effect.state_id,
                source_skill_id=skill.id,
                started_at=now,
                expires_at=now + timedelta(seconds=effect.duration_seconds),
                capabilities=[effect.capability] if effect.capability else [],
            ))
            applied.append(effect.state_id)
        return applied

    def _settle(self, profile: PlayerProfile) -> None:
        now = self.now()
        profile.exploration_effects = [effect for effect in profile.exploration_effects if effect.expires_at > now]

    def _grant(
        self,
        profile: PlayerProfile,
        skill_id: str,
        source: str,
        source_id: str | None,
    ) -> bool:
        self.catalog.get(skill_id)
        if skill_id in profile.learned_skills:
            return False
        profile.learned_skills[skill_id] = LearnedSkill(
            skill_id=skill_id,
            source=source,
            source_id=source_id,
            learned_at=self.now(),
        )
        if self.chronicle is not None:
            skill = self.catalog.get(skill_id)
            source_label = self.chronicle.text(f"skill_source_{source}")
            self.chronicle.record(
                profile,
                "skill",
                self.chronicle.text("skill_title", skill_name=skill.name),
                self.chronicle.text("skill_description", source=source_label),
                emoji="✦",
                source_id=f"skill:{skill_id}",
                details={"skill_id": skill_id, "source": source, "source_id": source_id},
            )
        return True

    def _ensure_valid_loadout(self, profile: PlayerProfile) -> None:
        profile.equipped_skill_ids = [
            skill_id for skill_id in profile.equipped_skill_ids
            if skill_id in profile.learned_skills
        ][:self.catalog.max_combat_slots]
        if profile.equipped_skill_ids:
            return
        candidates = [
            skill_id for skill_id in profile.learned_skills
            if self.catalog.get(skill_id).active and "combat" in self.catalog.get(skill_id).use_contexts
        ]
        profile.equipped_skill_ids = candidates[:self.catalog.max_combat_slots]
