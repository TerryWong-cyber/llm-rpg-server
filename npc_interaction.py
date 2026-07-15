"""Rule-layer orchestration for NPC dialogue, memory, story hooks, and combat.

This module owns deterministic relationship changes and trigger validation.  It
depends on repository and dialogue interfaces, not FastAPI or the battle graph.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from npc_dialogue import NPCDialogueService
from npc_models import NPCCombatProfile, NPCCombatTrigger, NPCProfile, NPCRelationship, StoryHook, TriggerCondition
from world_repository import InMemoryWorldRepository


INTENT_EFFECTS: Dict[str, Tuple[int, int, int, int]] = {
    # affinity, trust, respect, hostility
    "greeting": (0, 0, 0, 0),
    "inquiry": (1, 0, 0, 0),
    "gratitude": (2, 1, 0, -1),
    "offer_help": (4, 3, 1, -3),
    "trade": (0, 0, 0, 0),
    "insult": (-4, -2, -1, 5),
    "threat": (-7, -4, 0, 10),
}


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _model_dict(model) -> Dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def infer_intent(message: str) -> str:
    text = message.lower()
    if any(word in text for word in ["交出", "抢劫", "滚开", "动手", "袭击", "杀了", "威胁"]):
        return "threat"
    if any(word in text for word in ["蠢", "骗子", "废物", "闭嘴", "混蛋"]):
        return "insult"
    if any(word in text for word in ["帮你", "协助", "我可以帮", "愿意帮"]):
        return "offer_help"
    if any(word in text for word in ["谢谢", "感激", "多谢"]):
        return "gratitude"
    if any(word in text for word in ["买", "卖", "交易", "价钱"]):
        return "trade"
    if any(word in text for word in ["哪里", "为什么", "谁", "什么", "货单", "听说"]):
        return "inquiry"
    return "greeting"


class NPCInteractionService:
    def __init__(self, repository: InMemoryWorldRepository, dialogue_service: NPCDialogueService):
        self.repository = repository
        self.dialogue_service = dialogue_service

    def public_npc(self, npc: NPCProfile) -> Dict:
        return {
            "npc_id": npc.npc_id,
            "name": npc.name,
            "title": npc.title,
            "gender": npc.gender,
            "race": npc.race,
            "appearance": npc.appearance,
            "location": _model_dict(npc.location),
            "personality": npc.personality,
            "conversation_style": npc.conversation_style,
            "public_backstory": npc.backstory.public_summary,
            "has_combat_profile": npc.combat is not None,
            "combat_threat": npc.combat.threat if npc.combat else None,
        }

    def list_npcs(self, terrain_id: Optional[str] = None, cell_id: Optional[int] = None) -> List[Dict]:
        return [self.public_npc(npc) for npc in self.repository.list_npcs(terrain_id, cell_id)]

    def get_npc_view(self, npc_id: str, player_id: str) -> Dict:
        npc = self.repository.get_npc(npc_id)
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        return {"npc": self.public_npc(npc), "relationship": relationship}

    def interact(self, npc_id: str, player_id: str, message: str) -> Dict:
        npc = self.repository.get_npc(npc_id)
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        intent = infer_intent(message)
        self._apply_intent_effects(relationship, intent)
        relationship.interaction_count += 1

        memories = self.repository.list_npc_memories(npc_id, player_id, limit=6)
        available_hooks = self._available_hooks(npc, relationship, player_id)
        dialogue = self.dialogue_service.generate(
            npc=npc,
            relationship=relationship,
            memories=memories,
            player_message=message,
            available_hooks=available_hooks,
        )

        activated_hook = None
        valid_hook_ids = {hook.hook_id for hook in available_hooks}
        if dialogue.mentioned_hook_id in valid_hook_ids:
            activated_hook = next(hook for hook in available_hooks if hook.hook_id == dialogue.mentioned_hook_id)
            if activated_hook.hook_id not in relationship.active_story_hooks:
                relationship.active_story_hooks.append(activated_hook.hook_id)
                self.repository.record_world_fact(
                    f"{npc.name}的剧情线“{activated_hook.title}”出现了新的进展。",
                    tags=["story_hook", activated_hook.hook_id, npc_id],
                    facts={"npc_id": npc_id, "hook_id": activated_hook.hook_id},
                )

        combat_trigger = self._arm_combat_trigger(npc, relationship, message, player_id)
        self.repository.save_relationship(relationship)

        memory_summary = dialogue.memory_summary or f"玩家以{intent}的方式与{npc.name}交谈。"
        self.repository.append_shared_memory(
            npc_id=npc_id,
            player_id=player_id,
            npc_summary=f"{memory_summary} 玩家原话：{message[:120]}",
            player_summary=f"你与{npc.name}交谈：{dialogue.reply}",
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

    def start_combat(self, npc_id: str, player_id: str, trigger_id: str) -> Tuple[NPCProfile, NPCCombatProfile]:
        npc = self.repository.get_npc(npc_id)
        if npc.combat is None:
            raise ValueError("该 NPC 没有可用的战斗配置")
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        trigger = next((item for item in npc.combat_triggers if item.trigger_id == trigger_id), None)
        if trigger is None or trigger_id not in relationship.armed_combat_triggers:
            raise ValueError("战斗尚未被该 NPC 的互动规则触发")
        if trigger_id in relationship.consumed_combat_triggers and not trigger.repeatable:
            raise ValueError("该战斗触发器已被消耗")
        relationship.armed_combat_triggers.remove(trigger_id)
        relationship.consumed_combat_triggers.append(trigger_id)
        self.repository.save_relationship(relationship)
        self.repository.append_shared_memory(
            npc_id=npc_id,
            player_id=player_id,
            npc_summary=f"与玩家的冲突升级为战斗：{trigger.title}。",
            player_summary=f"你与{npc.name}的对峙升级为战斗：{trigger.title}。",
            tags=["combat", "combat_started", trigger_id],
            importance=5,
            facts={"trigger_id": trigger_id},
        )
        self.repository.record_world_fact(
            f"{npc.name}与一名冒险者在{npc.location.region}爆发了冲突。",
            tags=["combat", "combat_started", npc_id, trigger_id],
            facts={"npc_id": npc_id, "trigger_id": trigger_id},
        )
        return npc, npc.combat

    def record_combat_outcome(self, npc_id: str, player_id: str, player_won: Optional[bool]) -> None:
        npc = self.repository.get_npc(npc_id)
        relationship = self.repository.get_or_create_relationship(npc, player_id)
        if player_won is True:
            relationship.respect = _clamp(relationship.respect + 8, -100, 100)
            relationship.hostility = _clamp(relationship.hostility - 6, 0, 100)
            npc_memory = f"玩家在战斗中击败了我。{npc.name}不得不重新评估对方。"
            player_memory = f"你击败了{npc.name}；对方的敌意有所松动，但这件事不会被轻易遗忘。"
        elif player_won is False:
            relationship.hostility = _clamp(relationship.hostility + 5, 0, 100)
            npc_memory = f"玩家在战斗中败退。{npc.name}记住了这次胜利。"
            player_memory = f"你败给了{npc.name}；这场冲突很可能会影响下一次见面。"
        else:
            relationship.respect = _clamp(relationship.respect + 3, -100, 100)
            npc_memory = f"与玩家的冲突以两败俱伤收场。{npc.name}无法轻视这名对手。"
            player_memory = f"你与{npc.name}两败俱伤；这场平局会成为彼此记忆中的一根刺。"
        self.repository.save_relationship(relationship)
        self.repository.append_shared_memory(
            npc_id=npc_id,
            player_id=player_id,
            npc_summary=npc_memory,
            player_summary=player_memory,
            tags=["combat", "combat_victory" if player_won is True else "combat_defeat" if player_won is False else "combat_draw"],
            importance=5,
            facts={"player_won": player_won},
        )
        outcome = "被冒险者战胜" if player_won is True else "战胜了冒险者" if player_won is False else "与冒险者战平"
        self.repository.record_world_fact(
            f"{npc.name}在{npc.location.region}与一名冒险者交锋，最终{outcome}。",
            tags=["combat", "combat_outcome", npc_id],
            facts={"npc_id": npc_id, "player_won": player_won},
        )

    def record_player_event(self, player_id: str, summary: str, tags: List[str], importance: int = 2) -> None:
        """Integration point for maps, crafting, quest resolution, and future systems."""
        self.repository.append_player_memory(player_id, summary, tags, importance=importance)

    def player_memories(self, player_id: str) -> List:
        return self.repository.list_player_memories(player_id)

    def npc_memories(self, npc_id: str, player_id: str) -> List:
        self.repository.get_npc(npc_id)  # Keep 404 semantics consistent for callers.
        return self.repository.list_npc_memories(npc_id, player_id)

    def world_facts(self) -> List:
        return self.repository.list_world_facts()

    def _apply_intent_effects(self, relationship: NPCRelationship, intent: str) -> None:
        affinity, trust, respect, hostility = INTENT_EFFECTS[intent]
        relationship.affinity = _clamp(relationship.affinity + affinity, -100, 100)
        relationship.trust = _clamp(relationship.trust + trust, -100, 100)
        relationship.respect = _clamp(relationship.respect + respect, -100, 100)
        relationship.hostility = _clamp(relationship.hostility + hostility, 0, 100)

    def _available_hooks(self, npc: NPCProfile, relationship: NPCRelationship, player_id: str) -> List[StoryHook]:
        available = []
        for hook in npc.story_hooks:
            if relationship.affinity < hook.min_affinity or relationship.trust < hook.min_trust:
                continue
            if any(not self.repository.npc_has_memory_tag(npc.npc_id, player_id, tag) for tag in hook.requires_memory_tags):
                continue
            available.append(hook)
        return available

    def _arm_combat_trigger(
        self,
        npc: NPCProfile,
        relationship: NPCRelationship,
        message: str,
        player_id: str,
    ) -> Optional[Dict]:
        for trigger in npc.combat_triggers:
            if trigger.trigger_id in relationship.consumed_combat_triggers and not trigger.repeatable:
                continue
            if not all(self._matches_condition(condition, relationship, npc.npc_id, player_id, message) for condition in trigger.conditions):
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
            text = message.lower()
            return any(value.lower() in text for value in condition.values)
        if condition.kind == "relationship_at_least":
            return condition.field is not None and getattr(relationship, condition.field) >= (condition.threshold or 0)
        if condition.kind == "relationship_at_most":
            return condition.field is not None and getattr(relationship, condition.field) <= (condition.threshold or 0)
        if condition.kind == "memory_tag":
            return any(self.repository.npc_has_memory_tag(npc_id, player_id, value) for value in condition.values)
        if condition.kind == "relationship_flag":
            return any(value in relationship.flags for value in condition.values)
        return False
