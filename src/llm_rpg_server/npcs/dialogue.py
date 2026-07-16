from __future__ import annotations

from typing import Any

from llm_rpg_server.shared.config import ContentProvider

from .models import GeneratedDialogue, MemoryEntry, NPCProfile, NPCRelationship, StoryHook


class NPCDialogueService:
    def __init__(self, content: ContentProvider, llm: Any = None):
        self.content = content
        self.llm = llm
        self.rules = content.document("npcs/interaction_rules.json")

    def generate(
        self,
        *,
        npc: NPCProfile,
        relationship: NPCRelationship,
        memories: list[MemoryEntry],
        player_message: str,
        available_hooks: list[StoryHook],
    ) -> GeneratedDialogue:
        fallback = self._fallback(npc, relationship, player_message)
        if self.llm is None:
            return fallback
        hooks_text = "\n".join(
            self.content.text(
                "npc.prompt_hook",
                hook_id=hook.hook_id,
                title=hook.title,
                summary=hook.summary,
            )
            for hook in available_hooks
        )
        if not hooks_text:
            hooks_text = self.content.text("npc.no_hooks")
        memories_text = "\n".join(
            self.content.text("npc.prompt_memory", summary=memory.summary)
            for memory in memories[:6]
        )
        if not memories_text:
            memories_text = self.content.text("npc.first_memory")
        prompt_definition = self.content.prompt("npc_dialogue")
        try:
            from langchain_core.prompts import ChatPromptTemplate

            prompt = ChatPromptTemplate.from_messages([
                ("system", prompt_definition.system),
                ("human", prompt_definition.user),
            ])
            chain = prompt | self.llm.with_structured_output(GeneratedDialogue)
            result = chain.invoke({
                "name": npc.name,
                "title": npc.title,
                "race": npc.race,
                "gender": npc.gender,
                "appearance": npc.appearance,
                "region": npc.location.region,
                "landmark": npc.location.landmark,
                "personality": self.content.text("npc.list_separator").join(npc.personality),
                "conversation_style": npc.conversation_style,
                "public_summary": npc.backstory.public_summary,
                "personal_goal": npc.backstory.personal_goal,
                "affinity": relationship.affinity,
                "trust": relationship.trust,
                "respect": relationship.respect,
                "hostility": relationship.hostility,
                "memories": memories_text,
                "hooks": hooks_text,
                "player_message": player_message[:500],
            })
            valid_hooks = {hook.hook_id for hook in available_hooks}
            return GeneratedDialogue(
                reply=(result.reply or fallback.reply).strip()[:120],
                tone=(result.tone or fallback.tone).strip()[:24],
                mentioned_hook_id=result.mentioned_hook_id if result.mentioned_hook_id in valid_hooks else None,
                memory_summary=(result.memory_summary or "").strip()[:60],
            )
        except Exception:
            return fallback

    def _fallback(self, npc: NPCProfile, relationship: NPCRelationship, player_message: str) -> GeneratedDialogue:
        text = player_message.lower()
        if relationship.hostility >= 50:
            key = "hostile"
        elif any(word in text for word in self.rules["fallback_gratitude_keywords"]):
            key = "grateful"
        elif any(word in text for word in self.rules["fallback_greeting_keywords"]):
            key = "greeting"
        else:
            key = "default"
        fallback = self.content.document("narratives/zh-CN.json")["texts"]["npc"]["fallback"][key]
        return GeneratedDialogue(
            reply=fallback["reply"].format(npc_name=npc.name),
            tone=fallback["tone"],
            memory_summary=self.content.text("npc.memory.fallback_summary", npc_name=npc.name),
        )
