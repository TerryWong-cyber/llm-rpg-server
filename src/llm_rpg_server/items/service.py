from __future__ import annotations

from typing import Any

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import PlayerProfile, PlayerRepository
from llm_rpg_server.players.models import PersistentCombatStatus
from llm_rpg_server.shared.config import ContentProvider

from .models import EventItemOption, ItemUseOutcome, ItemUsePolicy, UseContext


class ItemService:
    """Server-authoritative item policy, matching, consumption, and non-combat effects."""

    def __init__(self, players: PlayerRepository, catalog: Catalog, content: ContentProvider):
        self.players = players
        self.catalog = catalog
        self.content = content

    def policy(self, definition: dict[str, Any]) -> ItemUsePolicy:
        return ItemUsePolicy.model_validate({
            "use_contexts": definition.get("use_contexts", []),
            "tradable": definition.get("tradable", True),
            "can_be_ingredient": definition.get("can_be_ingredient", True),
            "category": definition.get("category", "misc"),
            "tags": definition.get("tags", []),
        })

    def require_context(self, definition: dict[str, Any], context: UseContext) -> None:
        if context not in self.policy(definition).use_contexts:
            raise ValueError(self.content.text("errors.inventory.context_forbidden"))

    def use_outside_combat(self, player_id: str, item_id: str) -> tuple[PlayerProfile, ItemUseOutcome]:
        definition = self._owned_consumable(player_id, item_id)
        self.require_context(definition, "exploration")
        with self.players.transaction(player_id) as profile:
            self._require_quantity(profile, item_id)
            before_hp, before_mp, before_stamina = profile.current_hp, profile.current_mp, profile.stamina
            before_statuses = len(profile.combat_statuses)
            value = max(0, int(definition.get("val", 0)))
            effect_type = definition.get("type")
            if effect_type in {"heal_hp", "heal_both"}:
                profile.current_hp = min(profile.max_hp, profile.current_hp + value)
            if effect_type in {"heal_mp", "heal_both"}:
                profile.current_mp = min(profile.max_mp, profile.current_mp + value)
            stamina_restore = max(0, int(definition.get("stamina_restore", 0)))
            if stamina_restore:
                profile.stamina = min(profile.max_stamina, profile.stamina + stamina_restore)
            cleared_statuses = 0
            if definition.get("clear_negative_statuses"):
                profile.combat_statuses = [
                    status for status in profile.combat_statuses if "negative" not in status.tags
                ]
                cleared_statuses = before_statuses - len(profile.combat_statuses)
            applied_statuses = self._apply_exploration_statuses(
                profile,
                item_id,
                definition.get("exploration_statuses", []),
            )
            outcome = ItemUseOutcome(
                item_id=item_id,
                context="exploration",
                hp_restored=profile.current_hp - before_hp,
                mp_restored=profile.current_mp - before_mp,
                stamina_restored=profile.stamina - before_stamina,
                cleared_statuses=cleared_statuses,
                applied_statuses=applied_statuses,
            )
            if not any((
                outcome.hp_restored,
                outcome.mp_restored,
                outcome.stamina_restored,
                outcome.cleared_statuses,
                outcome.applied_statuses,
            )):
                raise ValueError(self.content.text("errors.inventory.no_effect"))
            self._consume(profile, item_id)
        return self.players.get(player_id), outcome

    def event_options(self, player_id: str, requirements: dict[str, Any]) -> list[EventItemOption]:
        profile = self.players.get(player_id)
        options: list[EventItemOption] = []
        for item_id, quantity in profile.inventory.items.items():
            if quantity <= 0:
                continue
            definition = self.catalog.item_definition("item", item_id)
            if definition is None or "world_event" not in self.policy(definition).use_contexts:
                continue
            score = self.match_score(definition, requirements)
            if score is None:
                continue
            options.append(EventItemOption(
                item_id=item_id,
                name=str(definition.get("name", item_id)),
                image_url=str(definition.get("image_url", "")),
                quantity=quantity,
                category=self.policy(definition).category,
                tags=self.policy(definition).tags,
                match_score=score,
            ))
        return sorted(options, key=lambda option: (-option.match_score, option.name, option.item_id))

    def consume_for_event(self, player_id: str, item_id: str, requirements: dict[str, Any]) -> ItemUseOutcome:
        definition = self._owned_consumable(player_id, item_id)
        self.require_context(definition, "world_event")
        if self.match_score(definition, requirements) is None:
            raise ValueError(self.content.text("errors.inventory.event_mismatch"))
        with self.players.transaction(player_id) as profile:
            self._require_quantity(profile, item_id)
            self._consume(profile, item_id)
        return ItemUseOutcome(item_id=item_id, context="world_event")

    @staticmethod
    def _apply_exploration_statuses(
        profile: PlayerProfile,
        item_id: str,
        definitions: list[dict[str, Any]],
    ) -> list[str]:
        applied: list[str] = []
        for raw in definitions:
            status = PersistentCombatStatus.model_validate({
                **raw,
                "source_id": f"item:{item_id}",
                "persistent": True,
            })
            profile.combat_statuses = [
                current for current in profile.combat_statuses if current.status_id != status.status_id
            ]
            profile.combat_statuses.append(status)
            applied.append(status.name)
        return applied

    @staticmethod
    def match_score(definition: dict[str, Any], requirements: dict[str, Any]) -> int | None:
        category = str(definition.get("category", "misc"))
        tags = set(map(str, definition.get("tags", [])))
        categories = set(map(str, requirements.get("categories", [])))
        all_tags = set(map(str, requirements.get("all_tags", [])))
        any_tags = set(map(str, requirements.get("any_tags", [])))
        if categories and category not in categories:
            return None
        if not all_tags.issubset(tags):
            return None
        if any_tags and not any_tags.intersection(tags):
            return None
        return len(all_tags) * 3 + len(any_tags.intersection(tags)) * 2 + int(category in categories)

    def _owned_consumable(self, player_id: str, item_id: str) -> dict[str, Any]:
        profile = self.players.get(player_id)
        self._require_quantity(profile, item_id)
        definition = self.catalog.item_definition("item", item_id)
        if definition is None:
            raise ValueError(self.content.text("errors.inventory.invalid_item"))
        return definition

    def _require_quantity(self, profile: PlayerProfile, item_id: str) -> None:
        if profile.inventory.items.get(item_id, 0) <= 0:
            raise ValueError(self.content.text("errors.inventory.not_owned"))

    @staticmethod
    def _consume(profile: PlayerProfile, item_id: str) -> None:
        profile.inventory.items[item_id] -= 1
        if profile.inventory.items[item_id] <= 0:
            profile.inventory.items.pop(item_id, None)
