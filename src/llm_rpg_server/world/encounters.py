from __future__ import annotations

import random

from llm_rpg_server.exploration.models import EncounterResult, EncounterRule, MapCell, MapInstance
from llm_rpg_server.exploration.repository import InMemoryEncounterHistory
from llm_rpg_server.npcs.service import NPCInteractionService
from llm_rpg_server.shared.config import ContentProvider

from .time import WorldClock


class EncounterService:
    def __init__(
        self,
        content: ContentProvider,
        npc_interactions: NPCInteractionService,
        history: InMemoryEncounterHistory | None = None,
        clock: WorldClock | None = None,
    ):
        self.npc_interactions = npc_interactions
        self.history = history or InMemoryEncounterHistory()
        self.clock = clock
        payload = content.document("maps/encounters.json")
        self.rules = sorted(
            (EncounterRule.model_validate(item) for item in payload["encounters"]),
            key=lambda item: item.priority,
            reverse=True,
        )

    def resolve(self, player_id: str, map_instance: MapInstance, cell: MapCell, trigger: str) -> EncounterResult | None:
        for rule in self.rules:
            if rule.trigger != trigger or not self._location_matches(rule, map_instance, cell):
                continue
            if not self.history.can_trigger(player_id, rule.encounter_id, rule.repeatable, rule.cooldown_seconds):
                continue
            if not self._conditions_match(rule, player_id):
                continue
            attempt = self.history.next_attempt(player_id, rule.encounter_id)
            rng = random.Random(f"{map_instance.seed}:{player_id}:{cell.cell_id}:{rule.encounter_id}:{attempt}")
            effective_chance = min(1.0, rule.chance * cell.npc_chance_multiplier)
            if rng.random() > effective_chance:
                continue
            self.history.record(player_id, rule.encounter_id)
            return EncounterResult(
                encounter_id=rule.encounter_id,
                npc_id=rule.npc_id,
                story_hook_id=rule.story_hook_id,
                trigger=rule.trigger,
            )
        return None

    def _conditions_match(self, rule: EncounterRule, player_id: str) -> bool:
        view = self.npc_interactions.get_npc_view(rule.npc_id, player_id)
        relationship = view["relationship"]
        now = self.clock.snapshot() if self.clock else None
        for condition in rule.conditions:
            if condition.kind == "relationship_at_least":
                if condition.field is None or getattr(relationship, condition.field) < (condition.threshold or 0):
                    return False
            elif condition.kind == "relationship_at_most":
                if condition.field is None or getattr(relationship, condition.field) > (condition.threshold or 0):
                    return False
            elif condition.kind == "relationship_flag":
                if not any(value in relationship.flags for value in condition.values):
                    return False
            elif condition.kind == "memory_tag":
                if not any(
                    self.npc_interactions.repository.npc_has_memory_tag(rule.npc_id, player_id, value)
                    for value in condition.values
                ):
                    return False
            elif condition.kind == "time_period":
                if now is None or now.period not in condition.values:
                    return False
            elif condition.kind == "season":
                if now is None or now.season not in condition.values:
                    return False
        return True

    @staticmethod
    def _location_matches(rule: EncounterRule, map_instance: MapInstance, cell: MapCell) -> bool:
        locations = rule.locations
        checks = (
            not locations.region_ids or map_instance.region_id in locations.region_ids,
            not locations.map_template_ids or map_instance.template_id in locations.map_template_ids,
            not locations.terrain_ids or cell.terrain_id in locations.terrain_ids,
            not locations.landmark_ids or cell.landmark_id in locations.landmark_ids,
            not locations.cell_ids or cell.cell_id in locations.cell_ids,
        )
        return all(checks)
