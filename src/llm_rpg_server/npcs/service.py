from __future__ import annotations

from typing import Any

from llm_rpg_server.shared.config import ContentProvider

from .dialogue import NPCDialogueService
from .models import NPCCombatProfile, NPCProfile, NPCRelationship, StoryHook, TriggerCondition
from .repository import WorldRepository


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


class NPCInteractionService:
    def __init__(
        self,
        repository: WorldRepository,
        dialogue_service: NPCDialogueService,
        content: ContentProvider,
    ):
        self.repository = repository
        self.dialogue_service = dialogue_service
        self.content = content
        self.rules = content.document("npcs/interaction_rules.json")

    def public_npc(self, npc: NPCProfile) -> dict[str, Any]:
        return {
            "npc_id": npc.npc_id,
            "name": npc.name,
            "title": npc.title,
            "gender": npc.gender,
            "race": npc.race,
            "appearance": npc.appearance,
            "location": npc.location.model_dump(mode="json"),
            "personality": npc.personality,
            "conversation_style": npc.conversation_style,
            "public_backstory": npc.backstory.public_summary,
            "has_combat_profile": npc.combat is not None,
            "combat_threat": npc.combat.threat if npc.combat else None,
        }

    def list_npcs(self, terrain_id: str | None = None, cell_id: int | None = None) -> list[dict[str, Any]]:
        return [self.public_npc(npc) for npc in self.repository.list_npcs(terrain_id, cell_id)]

    def get_npc_view(self, npc_id: str, player_id: str) -> dict[str, Any]:
        npc = self.repository.get_npc(npc_id)
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        return {"npc": self.public_npc(npc), "relationship": relationship}

    def interact(self, npc_id: str, player_id: str, message: str) -> dict[str, Any]:
        npc = self.repository.get_npc(npc_id)
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        intent = self.infer_intent(message)
        self._apply_intent_effects(relationship, intent)
        relationship.interaction_count += 1
        memories = self.repository.list_npc_memories(npc_id, player_id, limit=6)
        hooks = self._available_hooks(npc, relationship, player_id)
        dialogue = self.dialogue_service.generate(
            npc=npc,
            relationship=relationship,
            memories=memories,
            player_message=message,
            available_hooks=hooks,
        )
        activated_hook = self._activate_hook(dialogue.mentioned_hook_id, hooks, npc, relationship)
        combat_trigger = self._arm_combat_trigger(npc, relationship, message, player_id)
        self.repository.save_relationship(relationship)
        memory_summary = dialogue.memory_summary or self.content.text(
            "npc.memory.dialogue_default", intent=intent, npc_name=npc.name
        )
        self.repository.append_shared_memory(
            npc_id=npc_id,
            player_id=player_id,
            npc_summary=self.content.text(
                "npc.memory.npc_dialogue", summary=memory_summary, message=message[:120]
            ),
            player_summary=self.content.text(
                "npc.memory.player_dialogue", npc_name=npc.name, reply=dialogue.reply
            ),
            tags=["dialogue", intent] + (["combat_armed"] if combat_trigger else []),
            importance=4 if combat_trigger or activated_hook else 2,
            facts={"intent": intent, "activated_hook": activated_hook.hook_id if activated_hook else None},
        )
        return {
            "npc_id": npc_id,
            "reply": dialogue.reply,
            "tone": dialogue.tone,
            "intent": intent,
            "relationship": relationship,
            "activated_story_hook": activated_hook,
            "combat_trigger": combat_trigger,
        }

    def infer_intent(self, message: str) -> str:
        text = message.lower()
        for intent in self.rules["intent_order"]:
            if any(word.lower() in text for word in self.rules["intents"][intent]["keywords"]):
                return intent
        return "greeting"

    def start_combat(self, npc_id: str, player_id: str, trigger_id: str) -> tuple[NPCProfile, NPCCombatProfile]:
        npc = self.repository.get_npc(npc_id)
        if npc.combat is None:
            raise ValueError(self.content.text("errors.npc.no_combat"))
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        trigger = next((item for item in npc.combat_triggers if item.trigger_id == trigger_id), None)
        if trigger is None or trigger_id not in relationship.armed_combat_triggers:
            raise ValueError(self.content.text("errors.npc.combat_locked"))
        if trigger_id in relationship.consumed_combat_triggers and not trigger.repeatable:
            raise ValueError(self.content.text("errors.npc.combat_consumed"))
        relationship.armed_combat_triggers.remove(trigger_id)
        relationship.consumed_combat_triggers.append(trigger_id)
        self.repository.save_relationship(relationship)
        self.repository.append_shared_memory(
            npc_id=npc_id,
            player_id=player_id,
            npc_summary=self.content.text("npc.memory.combat_start_npc", trigger_title=trigger.title),
            player_summary=self.content.text(
                "npc.memory.combat_start_player", npc_name=npc.name, trigger_title=trigger.title
            ),
            tags=["combat", "combat_started", trigger_id],
            importance=5,
            facts={"trigger_id": trigger_id},
        )
        self.repository.record_world_fact(
            self.content.text("npc.memory.combat_start_world", npc_name=npc.name, region=npc.location.region),
            tags=["combat", "combat_started", npc_id, trigger_id],
            facts={"npc_id": npc_id, "trigger_id": trigger_id},
        )
        return npc, npc.combat

    def record_combat_outcome(self, npc_id: str, player_id: str, player_won: bool | None) -> None:
        npc = self.repository.get_npc(npc_id)
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        if player_won is True:
            relationship.respect = clamp(relationship.respect + 8, -100, 100)
            relationship.hostility = clamp(relationship.hostility - 6, 0, 100)
            outcome_key, tag = "victory", "combat_victory"
        elif player_won is False:
            relationship.hostility = clamp(relationship.hostility + 5, 0, 100)
            outcome_key, tag = "defeat", "combat_defeat"
        else:
            relationship.respect = clamp(relationship.respect + 3, -100, 100)
            outcome_key, tag = "draw", "combat_draw"
        self.repository.save_relationship(relationship)
        self.repository.append_shared_memory(
            npc_id=npc_id,
            player_id=player_id,
            npc_summary=self.content.text(f"npc.memory.{outcome_key}_npc", npc_name=npc.name),
            player_summary=self.content.text(f"npc.memory.{outcome_key}_player", npc_name=npc.name),
            tags=["combat", tag],
            importance=5,
            facts={"player_won": player_won},
        )
        outcome = self.content.text(f"npc.memory.outcome_{outcome_key}")
        self.repository.record_world_fact(
            self.content.text(
                "npc.memory.combat_outcome_world", npc_name=npc.name, region=npc.location.region, outcome=outcome
            ),
            tags=["combat", "combat_outcome", npc_id],
            facts={"npc_id": npc_id, "player_won": player_won},
        )

    def record_player_event(self, player_id: str, summary: str, tags: list[str], importance: int = 2) -> None:
        self.repository.append_player_memory(player_id, summary, tags, importance=importance)

    def player_memories(self, player_id: str) -> list:
        return self.repository.list_player_memories(player_id)

    def npc_memories(self, npc_id: str, player_id: str) -> list:
        self.repository.get_npc(npc_id)
        return self.repository.list_npc_memories(npc_id, player_id)

    def world_facts(self) -> list:
        return self.repository.list_world_facts()

    def _apply_intent_effects(self, relationship: NPCRelationship, intent: str) -> None:
        affinity, trust, respect, hostility = self.rules["intents"][intent]["effects"]
        relationship.affinity = clamp(relationship.affinity + affinity, -100, 100)
        relationship.trust = clamp(relationship.trust + trust, -100, 100)
        relationship.respect = clamp(relationship.respect + respect, -100, 100)
        relationship.hostility = clamp(relationship.hostility + hostility, 0, 100)

    def _activate_hook(
        self,
        hook_id: str | None,
        hooks: list[StoryHook],
        npc: NPCProfile,
        relationship: NPCRelationship,
    ) -> StoryHook | None:
        hook = next((item for item in hooks if item.hook_id == hook_id), None)
        if hook and hook.hook_id not in relationship.active_story_hooks:
            relationship.active_story_hooks.append(hook.hook_id)
            self.repository.record_world_fact(
                self.content.text("npc.memory.story_progress", npc_name=npc.name, hook_title=hook.title),
                tags=["story_hook", hook.hook_id, npc.npc_id],
                facts={"npc_id": npc.npc_id, "hook_id": hook.hook_id},
            )
        return hook

    def _available_hooks(
        self,
        npc: NPCProfile,
        relationship: NPCRelationship,
        player_id: str,
    ) -> list[StoryHook]:
        return [
            hook
            for hook in npc.story_hooks
            if relationship.affinity >= hook.min_affinity
            and relationship.trust >= hook.min_trust
            and all(
                self.repository.npc_has_memory_tag(npc.npc_id, player_id, tag)
                for tag in hook.requires_memory_tags
            )
        ]

    def _arm_combat_trigger(
        self,
        npc: NPCProfile,
        relationship: NPCRelationship,
        message: str,
        player_id: str,
    ) -> dict[str, str] | None:
        for trigger in npc.combat_triggers:
            if trigger.trigger_id in relationship.consumed_combat_triggers and not trigger.repeatable:
                continue
            if not all(
                self._matches_condition(item, relationship, npc.npc_id, player_id, message)
                for item in trigger.conditions
            ):
                continue
            if trigger.trigger_id not in relationship.armed_combat_triggers:
                relationship.armed_combat_triggers.append(trigger.trigger_id)
            return {"trigger_id": trigger.trigger_id, "title": trigger.title, "intro": trigger.intro}
        return None

    def _matches_condition(
        self,
        condition: TriggerCondition,
        relationship: NPCRelationship,
        npc_id: str,
        player_id: str,
        message: str,
    ) -> bool:
        if condition.kind == "message_contains":
            return any(value.lower() in message.lower() for value in condition.values)
        if condition.kind == "relationship_at_least":
            return condition.field is not None and getattr(relationship, condition.field) >= (condition.threshold or 0)
        if condition.kind == "relationship_at_most":
            return condition.field is not None and getattr(relationship, condition.field) <= (condition.threshold or 0)
        if condition.kind == "memory_tag":
            return any(self.repository.npc_has_memory_tag(npc_id, player_id, value) for value in condition.values)
        if condition.kind == "relationship_flag":
            return any(value in relationship.flags for value in condition.values)
        return False
