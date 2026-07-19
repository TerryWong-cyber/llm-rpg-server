from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_rpg_server.combat import CombatSessionService
from llm_rpg_server.combat.rooms import GameRoom
from llm_rpg_server.exploration import ExplorationService, MapInstance
from llm_rpg_server.exploration.models import WorldEventResult
from llm_rpg_server.items import ItemService
from llm_rpg_server.monsters import MonsterCatalog
from llm_rpg_server.npcs import NPCInteractionService


@dataclass(slots=True)
class EventInteractionOutcome:
    current: MapInstance
    event: WorldEventResult
    interaction: dict[str, Any]
    combat_room: GameRoom | None = None


class WorldEventCoordinator:
    """Routes an already-triggered map event into NPC, quest, or combat domains."""

    def __init__(
        self,
        exploration: ExplorationService,
        npcs: NPCInteractionService,
        monsters: MonsterCatalog,
        combat: CombatSessionService,
        items: ItemService,
    ):
        self.exploration = exploration
        self.npcs = npcs
        self.monsters = monsters
        self.combat = combat
        self.items = items
        self._validate_references()

    def public_participant(self, rule: dict[str, Any]) -> dict[str, Any] | None:
        actor = rule.get("actor")
        if not actor:
            return None
        actor_type, actor_id = actor.get("type"), actor.get("id")
        if actor_type == "npc":
            npc = self.npcs.repository.get_npc(actor_id)
            return {"type": "npc", **self.npcs.public_npc(npc)}
        if actor_type == "monster":
            return {"type": "monster", **self.monsters.public_view(actor_id)}
        return None

    def discover_participant(self, rule: dict[str, Any], player_id: str) -> None:
        actor = rule.get("actor") or {}
        if actor.get("type") == "npc" and actor.get("id"):
            self.npcs.discover(actor["id"], player_id)

    def perform(
        self,
        player_id: str,
        event_id: str,
        action_id: str,
        item_id: str | None = None,
    ) -> EventInteractionOutcome:
        rule, action = self.exploration.validate_event_action(player_id, event_id, action_id)
        kind = action.get("kind", "narrative")
        interaction: dict[str, Any] = {"type": kind}
        room: GameRoom | None = None
        if action.get("target_id") and kind in {"open_npc", "start_quest", "npc_combat"}:
            self.npcs.discover(action["target_id"], player_id)

        if kind == "use_item":
            if not item_id:
                raise ValueError(self.items.content.text("errors.inventory.event_item_required"))
            usage = self.items.consume_for_event(
                player_id,
                item_id,
                action.get("item_requirements", {}),
            )
            interaction.update({"item_id": item_id, "item_use": usage.model_dump(mode="json")})
        elif kind == "open_npc":
            interaction["npc_id"] = action["target_id"]
        elif kind == "start_quest":
            hook = self.npcs.activate_story_hook(
                action["target_id"],
                player_id,
                action["hook_id"],
                source=event_id,
            )
            interaction.update({
                "npc_id": action["target_id"],
                "story_hook": hook.model_dump(mode="json"),
            })
        elif kind == "npc_combat":
            self.npcs.arm_event_combat(
                action["target_id"],
                player_id,
                action["trigger_id"],
                source=event_id,
            )
            room = self.combat.start_npc_combat(
                player_id,
                action["target_id"],
                action["trigger_id"],
            )
            interaction["npc_id"] = action["target_id"]
        elif kind == "monster_combat":
            room = self.combat.start_monster_combat(
                player_id,
                action["target_id"],
                event_id,
            )
            interaction["monster_id"] = action["target_id"]

        current, event = self.exploration.event_action(player_id, event_id, action_id)
        return EventInteractionOutcome(current, event, interaction, room)

    def item_options(self, player_id: str, event_id: str) -> dict[str, list[dict[str, Any]]]:
        rule = next((item for item in self.exploration.event_rules if item["event_id"] == event_id), None)
        if rule is None:
            return {}
        return {
            action["action_id"]: [
                option.model_dump(mode="json")
                for option in self.items.event_options(player_id, action.get("item_requirements", {}))
            ]
            for action in rule.get("actions", [])
            if action.get("kind") == "use_item"
        }

    def _validate_references(self) -> None:
        npc_ids = {npc.npc_id for npc in self.npcs.repository.list_npcs()}
        monster_ids = {monster.monster_id for monster in self.monsters.list_all()}
        for rule in self.exploration.event_rules:
            event_id = rule["event_id"]
            actor = rule.get("actor")
            if actor:
                actor_type, actor_id = actor.get("type"), actor.get("id")
                if (actor_type == "npc" and actor_id not in npc_ids) or (
                    actor_type == "monster" and actor_id not in monster_ids
                ):
                    raise ValueError(f"World event {event_id} references an unknown actor")
            for action in rule.get("actions", []):
                kind = action.get("kind", "narrative")
                target_id = action.get("target_id")
                if kind in {"open_npc", "start_quest", "npc_combat"}:
                    if target_id not in npc_ids:
                        raise ValueError(f"World event {event_id} references an unknown NPC")
                    npc = self.npcs.repository.get_npc(target_id)
                    if kind == "start_quest" and action.get("hook_id") not in {
                        hook.hook_id for hook in npc.story_hooks
                    }:
                        raise ValueError(f"World event {event_id} references an unknown story hook")
                    if kind == "npc_combat" and action.get("trigger_id") not in {
                        trigger.trigger_id for trigger in npc.combat_triggers
                    }:
                        raise ValueError(f"World event {event_id} references an unknown combat trigger")
                if kind == "monster_combat" and target_id not in monster_ids:
                    raise ValueError(f"World event {event_id} references an unknown monster")
                if kind == "use_item" and not action.get("item_requirements"):
                    raise ValueError(f"World event {event_id} has item action without requirements")
