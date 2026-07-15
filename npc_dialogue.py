"""LLM adapter for NPC dialogue.

The model may choose wording, emotional tone, and which already-valid story
hook to mention.  It never receives authority over inventory, rewards,
relationship numbers, or combat results.
"""

from __future__ import annotations

from typing import List, Optional

from npc_models import GeneratedDialogue, MemoryEntry, NPCProfile, NPCRelationship, StoryHook


def _compact_memories(memories: List[MemoryEntry]) -> str:
    if not memories:
        return "（这是你们的第一次交谈。）"
    return "\n".join(f"- {memory.summary}" for memory in memories[:6])


class NPCDialogueService:
    def __init__(self, llm=None):
        self.llm = llm

    def generate(
        self,
        *,
        npc: NPCProfile,
        relationship: NPCRelationship,
        memories: List[MemoryEntry],
        player_message: str,
        available_hooks: List[StoryHook],
    ) -> GeneratedDialogue:
        """Return a safe, persona-consistent line, with a deterministic fallback."""
        fallback = self._fallback(npc, relationship, player_message)
        if self.llm is None:
            return fallback

        hooks_text = "\n".join(
            f"- {hook.hook_id}: {hook.title}。{hook.summary}" for hook in available_hooks
        ) or "（目前没有可提及的剧情线。）"
        try:
            # Keep the domain layer importable in admin tools and tests that do
            # not have the optional LLM runtime installed.
            from langchain_core.prompts import ChatPromptTemplate

            prompt = ChatPromptTemplate.from_messages([
                ("system", """你是一个中文奇幻 RPG 的 NPC 对话写手。只输出结构化结果。
你的职责是让角色自然、具体、记得玩家过去的互动。
硬性边界：
1. 不得编造不存在的物品、奖励、任务完成、数值变化或世界事实。
2. 不得泄露 NPC 的私密秘密；即使被追问，也只能用符合性格的闪避或暗示回应。
3. reply 只写 NPC 对玩家说的话，最多 120 个汉字。
4. mentioned_hook_id 只能是“当前可提及剧情线”中的 ID；不提及则为 null。
5. memory_summary 只概括本轮互动（最多 60 字），不写关系数值。"""),
            ("human", """【NPC公开资料】
名字：{name}（{title}）
种族/性别：{race}/{gender}
外貌：{appearance}
地点：{region}，{landmark}
性格：{personality}
说话方式：{conversation_style}
公开经历：{public_summary}
当前目标：{personal_goal}

【NPC对玩家的态度】
亲近 {affinity}，信任 {trust}，敬重 {respect}，敌意 {hostility}

【NPC记得的互动】
{memories}

【当前可提及剧情线】
{hooks}

【玩家刚说】
{player_message}"""),
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
                "personality": "、".join(npc.personality),
                "conversation_style": npc.conversation_style,
                "public_summary": npc.backstory.public_summary,
                "personal_goal": npc.backstory.personal_goal,
                "affinity": relationship.affinity,
                "trust": relationship.trust,
                "respect": relationship.respect,
                "hostility": relationship.hostility,
                "memories": _compact_memories(memories),
                "hooks": hooks_text,
                "player_message": player_message[:500],
            })
            valid_hook_ids = {hook.hook_id for hook in available_hooks}
            mentioned_hook_id = result.mentioned_hook_id if result.mentioned_hook_id in valid_hook_ids else None
            reply = (result.reply or fallback.reply).strip()[:120]
            return GeneratedDialogue(
                reply=reply,
                tone=(result.tone or fallback.tone).strip()[:24],
                mentioned_hook_id=mentioned_hook_id,
                memory_summary=(result.memory_summary or "").strip()[:60],
            )
        except Exception:
            # An unavailable local model must never break game progression.
            return fallback

    @staticmethod
    def _fallback(npc: NPCProfile, relationship: NPCRelationship, player_message: str) -> GeneratedDialogue:
        text = player_message.lower()
        if relationship.hostility >= 50:
            reply = f"{npc.name}没有回答，只把重心悄悄压低：‘把你的手放在我看得见的地方。’"
            tone = "戒备"
        elif any(word in text for word in ["谢谢", "帮忙", "协助"]):
            reply = f"{npc.name}略一点头：‘记住你刚才的话。这里的人，很少把承诺说第二遍。’"
            tone = "缓和"
        elif any(word in text for word in ["你好", "在吗", "打扰"]):
            reply = f"{npc.name}抬眼打量了你一会儿：‘说吧。风不会无缘无故把人吹到这里。’"
            tone = "审视"
        else:
            reply = f"{npc.name}沉默片刻，才说：‘我听着。只要你的问题值得我回答。’"
            tone = "平静"
        return GeneratedDialogue(reply=reply, tone=tone, memory_summary=f"玩家与{npc.name}进行了一次交谈。")
