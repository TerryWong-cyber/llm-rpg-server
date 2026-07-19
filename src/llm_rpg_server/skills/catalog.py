from __future__ import annotations

from copy import deepcopy
from typing import Any

from llm_rpg_server.shared.config import ContentProvider

from .models import SkillDefinition, SkillRulesDocument, TrainerOffer


class SkillCatalog:
    def __init__(self, content: ContentProvider):
        self.rules = SkillRulesDocument.model_validate(content.document("skills/skills.json"))

    @property
    def max_combat_slots(self) -> int:
        return self.rules.max_combat_slots

    def get(self, skill_id: str) -> SkillDefinition:
        try:
            return self.rules.skills[skill_id].model_copy(deep=True)
        except KeyError as exc:
            raise KeyError(skill_id) from exc

    def public_view(self) -> dict[str, dict[str, Any]]:
        return {
            skill_id: deepcopy(skill.model_dump(mode="json"))
            for skill_id, skill in self.rules.skills.items()
        }

    def trainer_offers(self, npc_id: str) -> list[TrainerOffer]:
        return [offer.model_copy(deep=True) for offer in self.rules.trainers.get(npc_id, [])]

    def world_events(self) -> list[dict[str, Any]]:
        return deepcopy(self.rules.world_events)
