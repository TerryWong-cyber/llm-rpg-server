from __future__ import annotations

from llm_rpg_server.shared.config import ContentProvider

from .models import NPCProfile
from .repository import WorldRepository


def load_npcs(content: ContentProvider) -> list[NPCProfile]:
    payload = content.document("npcs/demo_npcs.json")
    return [NPCProfile.model_validate(item) for item in payload["npcs"]]


def seed_npcs(
    repository: WorldRepository,
    content: ContentProvider,
    *,
    overwrite: bool = False,
) -> list[NPCProfile]:
    return [repository.register_npc(npc, overwrite=overwrite) for npc in load_npcs(content)]
