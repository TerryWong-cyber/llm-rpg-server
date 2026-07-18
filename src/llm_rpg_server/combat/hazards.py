from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from llm_rpg_server.shared.config import ContentProvider

from .rules import CombatRulebook


class FallHazardEstimate(BaseModel):
    height_m: float = Field(default=3, ge=0, le=500)
    mass_kg: float = Field(default=70, ge=1, le=1000)
    ground_hardness: float = Field(default=0.7, ge=0.1, le=1.5)
    mitigation: float = Field(default=0, ge=0, le=0.9)
    reason: str = "使用安全的默认环境参数。"


class FallHazardResult(BaseModel):
    estimate: FallHazardEstimate
    damage: int = Field(ge=0)


class EnvironmentHazardService:
    """Lets an LLM fill bounded missing measurements, never the damage result."""

    def __init__(self, rules: CombatRulebook, content: ContentProvider, llm: Any):
        self.rules = rules
        self.content = content
        self.llm = llm

    def resolve_fall(
        self,
        description: str,
        *,
        height_m: float | None = None,
        mass_kg: float | None = None,
        ground_hardness: float | None = None,
        mitigation: float | None = None,
        config: RunnableConfig | None = None,
    ) -> FallHazardResult:
        known = {
            "height_m": height_m,
            "mass_kg": mass_kg,
            "ground_hardness": ground_hardness,
            "mitigation": mitigation,
        }
        estimate = self._estimate(description, known, config or {})
        for key, value in known.items():
            if value is not None:
                setattr(estimate, key, value)
        bounded = estimate.model_dump()
        bounded["height_m"] = min(500, max(0, float(bounded["height_m"])))
        bounded["mass_kg"] = min(1000, max(1, float(bounded["mass_kg"])))
        bounded["ground_hardness"] = min(1.5, max(0.1, float(bounded["ground_hardness"])))
        bounded["mitigation"] = min(0.9, max(0, float(bounded["mitigation"])))
        estimate = FallHazardEstimate.model_validate(bounded)
        damage = self.rules.fall_damage(
            estimate.height_m,
            estimate.mass_kg,
            estimate.ground_hardness,
            estimate.mitigation,
        )
        return FallHazardResult(estimate=estimate, damage=damage)

    def _estimate(
        self,
        description: str,
        known: dict[str, float | None],
        config: RunnableConfig,
    ) -> FallHazardEstimate:
        fallback = FallHazardEstimate(
            height_m=known["height_m"] if known["height_m"] is not None else 3,
            mass_kg=known["mass_kg"] if known["mass_kg"] is not None else 70,
            ground_hardness=(
                known["ground_hardness"] if known["ground_hardness"] is not None else 0.7
            ),
            mitigation=known["mitigation"] if known["mitigation"] is not None else 0,
        )
        if self.llm is None or all(value is not None for value in known.values()):
            return fallback
        definition = self.content.prompt("environment_hazard")
        try:
            prompt = ChatPromptTemplate.from_messages([("system", definition.system), ("human", definition.user)])
            result = (prompt | self.llm.with_structured_output(FallHazardEstimate)).invoke(
                {
                    "description": description,
                    "height_m": known["height_m"] if known["height_m"] is not None else "未知",
                    "mass_kg": known["mass_kg"] if known["mass_kg"] is not None else "未知",
                    "ground_hardness": (
                        known["ground_hardness"] if known["ground_hardness"] is not None else "未知"
                    ),
                    "mitigation": known["mitigation"] if known["mitigation"] is not None else "未知",
                },
                config=config,
            )
            return result
        except Exception:
            return fallback
