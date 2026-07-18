from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from llm_rpg_server.shared.config import ContentProvider

from .models import PlayerProfile, QuestProgress
from .repository import PlayerRepository
from .service import PlayerService


class LevelCurve(BaseModel):
    base_experience: int = Field(gt=0)
    exponent: float = Field(ge=1)
    attribute_points_per_level: int = Field(gt=0)
    maximum_level: int = Field(gt=1)


class BaseResources(BaseModel):
    hp: int = Field(gt=0)
    mp: int = Field(ge=0)


class ExperienceRewards(BaseModel):
    random_pve: int = Field(ge=0)
    pvp_victory: int = Field(ge=0)
    npc_base: int = Field(ge=0)
    monster_base: int = Field(ge=0)
    per_threat: int = Field(ge=0)


class GrowthRulesDocument(BaseModel):
    schema_version: str
    level_curve: LevelCurve
    base_resources: BaseResources
    experience_rewards: ExperienceRewards
    allocatable_attributes: list[str]


class GrowthService:
    """Server-authoritative experience, level, attribute, and quest progression."""

    def __init__(
        self,
        players: PlayerRepository,
        player_service: PlayerService,
        content: ContentProvider,
    ):
        self.players = players
        self.player_service = player_service
        self.content = content
        self.rules = GrowthRulesDocument.model_validate(
            content.document("progression/rules.json")
        )

    def experience_to_next(self, level: int) -> int:
        curve = self.rules.level_curve
        return max(1, round(curve.base_experience * (max(1, level) ** curve.exponent)))

    def public_progress(self, profile: PlayerProfile) -> dict[str, Any]:
        return {
            "race_id": profile.race_id,
            "level": profile.level,
            "experience": profile.experience,
            "total_experience": profile.total_experience,
            "experience_to_next": self.experience_to_next(profile.level),
            "attribute_points": profile.attribute_points,
            "attributes": profile.attributes.model_dump(mode="json"),
        }

    def apply_experience(self, profile: PlayerProfile, amount: int) -> dict[str, Any]:
        gained = max(0, int(amount))
        profile.experience += gained
        profile.total_experience += gained
        levels_gained = 0
        curve = self.rules.level_curve
        while (
            profile.level < curve.maximum_level
            and profile.experience >= self.experience_to_next(profile.level)
        ):
            profile.experience -= self.experience_to_next(profile.level)
            profile.level += 1
            profile.attribute_points += curve.attribute_points_per_level
            levels_gained += 1
        if profile.level >= curve.maximum_level:
            profile.experience = min(
                profile.experience,
                self.experience_to_next(profile.level),
            )
        profile.experience_to_next = self.experience_to_next(profile.level)
        return {
            "experience": gained,
            "levels_gained": levels_gained,
            "level": profile.level,
            "attribute_points": profile.attribute_points,
        }

    def award_once(
        self,
        player_id: str,
        amount: int,
        operation_id: str,
    ) -> tuple[bool, dict[str, Any] | None]:
        return self.players.update_once(
            player_id,
            operation_id,
            lambda profile: self.apply_experience(profile, amount),
        )

    def allocate(self, player_id: str, allocations: dict[str, int]) -> PlayerProfile:
        allowed = set(self.rules.allocatable_attributes)
        normalized: dict[str, int] = {}
        for name, raw in allocations.items():
            if name not in allowed or isinstance(raw, bool) or int(raw) != raw or raw < 0:
                raise ValueError(self.content.text("errors.growth.invalid_allocation"))
            if raw:
                normalized[name] = int(raw)
        spent = sum(normalized.values())
        if spent <= 0:
            raise ValueError(self.content.text("errors.growth.empty_allocation"))
        with self.players.transaction(player_id) as profile:
            if spent > profile.attribute_points:
                raise ValueError(self.content.text("errors.growth.insufficient_points"))
            for name, amount in normalized.items():
                setattr(profile.attributes, name, getattr(profile.attributes, name) + amount)
            profile.attribute_points -= spent
            self.player_service.recalculate_resources(profile, restore_gains=True)
        return self.players.get(player_id)

    def start_quest(self, player_id: str, npc_id: str, hook: Any) -> None:
        hook_id = str(hook.hook_id)
        with self.players.transaction(player_id) as profile:
            if hook_id in profile.completed_quests or hook_id in profile.active_quests:
                return
            profile.active_quests[hook_id] = QuestProgress(
                hook_id=hook_id,
                npc_id=npc_id,
                title=str(hook.title),
                summary=str(hook.summary),
                xp_reward=int(getattr(hook, "xp_reward", 0)),
                requirements=[
                    requirement.model_dump(mode="json")
                    for requirement in getattr(hook, "requirements", [])
                ],
            )

    def complete_quest(self, player_id: str, npc_id: str, hook_id: str) -> dict[str, Any]:
        operation_id = f"quest_reward:{npc_id}:{hook_id}"

        def complete(profile: PlayerProfile) -> dict[str, Any]:
            quest = profile.active_quests.get(hook_id)
            if quest is None or quest.npc_id != npc_id:
                raise ValueError(self.content.text("errors.growth.quest_not_active"))
            self._require_quest_objectives(profile, quest)
            self._consume_quest_items(profile, quest)
            reward = self.apply_experience(profile, quest.xp_reward)
            profile.active_quests.pop(hook_id, None)
            if hook_id not in profile.completed_quests:
                profile.completed_quests.append(hook_id)
            return reward

        awarded, reward = self.players.update_once(player_id, operation_id, complete)
        if not awarded:
            raise ValueError(self.content.text("errors.growth.quest_completed"))
        return reward or {}

    def _require_quest_objectives(
        self,
        profile: PlayerProfile,
        quest: QuestProgress,
    ) -> None:
        for requirement in quest.requirements:
            kind = requirement.get("kind")
            if kind == "inventory":
                collection = (
                    profile.inventory.materials
                    if requirement.get("item_type") == "material"
                    else profile.inventory.items
                )
                if collection.get(str(requirement.get("item_id")), 0) < int(
                    requirement.get("quantity", 1)
                ):
                    raise ValueError(self.content.text("errors.growth.quest_requirements"))
            elif kind == "region":
                current_region = (profile.current_map or {}).get("region_id")
                if current_region != requirement.get("region_id"):
                    raise ValueError(self.content.text("errors.growth.quest_requirements"))

    @staticmethod
    def _consume_quest_items(profile: PlayerProfile, quest: QuestProgress) -> None:
        for requirement in quest.requirements:
            if requirement.get("kind") != "inventory" or not requirement.get("consume", False):
                continue
            collection = (
                profile.inventory.materials
                if requirement.get("item_type") == "material"
                else profile.inventory.items
            )
            item_id = str(requirement["item_id"])
            collection[item_id] -= int(requirement.get("quantity", 1))
            if collection[item_id] <= 0:
                collection.pop(item_id, None)
