from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

from llm_rpg_server.shared.config import ContentProvider

from .models import CombatEffectivenessJudgement, EffectivenessAssessment


class EffectivenessJudge:
    """Evaluates qualitative action feasibility without owning combat numbers."""

    def __init__(self, content: ContentProvider, llm: Any):
        self.content = content
        self.llm = llm

    def evaluate(self, state: dict[str, Any], config: RunnableConfig) -> CombatEffectivenessJudgement:
        fallback = CombatEffectivenessJudgement(
            player=self._deterministic(
                state["player_action"], state.get("player_statuses", []), state["ai_action"]
            ),
            opponent=self._deterministic(
                state["ai_action"], state.get("ai_statuses", []), state["player_action"]
            ),
        )
        if state.get("game_mode") == "PvP" or self.llm is None:
            return fallback
        definition = self.content.prompt("combat_effectiveness")
        try:
            prompt = ChatPromptTemplate.from_messages([("system", definition.system), ("human", definition.user)])
            result = (prompt | self.llm.with_structured_output(CombatEffectivenessJudgement)).invoke(
                {
                    "environment": state.get("environment", "未知战场"),
                    "environment_tags": ", ".join(state.get("environment_context", {}).get("tags", [])) or "无",
                    "player_profile": self._qualitative_profile(state, "player"),
                    "player_action": self._action_description(state["player_action"]),
                    "player_statuses": self._status_names(state.get("player_statuses", [])),
                    "opponent_profile": self._qualitative_profile(state, "ai"),
                    "opponent_action": self._action_description(state["ai_action"]),
                    "opponent_statuses": self._status_names(state.get("ai_statuses", [])),
                },
                config=config,
            )
            result.player.score = self._clamp(result.player.score)
            result.opponent.score = self._clamp(result.opponent.score)
            return result
        except Exception:
            return fallback

    def _deterministic(
        self,
        action: dict[str, Any],
        statuses: list[dict[str, Any]],
        target_action: dict[str, Any],
    ) -> EffectivenessAssessment:
        if any("incapacitating" in item.get("tags", []) for item in statuses):
            return EffectivenessAssessment(
                score=0,
                reason="行动者受到昏迷等强制控制，无法完成招式。",
                factors=["强制控制"],
            )
        if target_action.get("type") == "defense" and action.get("type") in {"attack", "skill"}:
            return EffectivenessAssessment(score=2, reason="攻击被对手的战术防御显著削弱。", factors=["战术防御"])
        return EffectivenessAssessment()

    @staticmethod
    def _qualitative_profile(state: dict[str, Any], prefix: str) -> str:
        character = state.get(f"{prefix}_class") or {}
        weapon = state.get(f"{prefix}_weapon") or {}
        traits = state.get(
            f"{prefix}_traits",
            character.get("traits", character.get("tags", [])),
        )
        return (
            f"身份：{character.get('name', '未知')}；描述：{character.get('desc', '无')}；"
            f"武器：{weapon.get('name', '无')}；攻击距离：{weapon.get('range', '未知')}；"
            f"特征：{', '.join(traits) or '无'}"
        )

    @staticmethod
    def _action_description(action: dict[str, Any]) -> str:
        return f"{action.get('name', '未知动作')}；{action.get('description', '无额外描述')}"

    @staticmethod
    def _status_names(statuses: list[dict[str, Any]]) -> str:
        return "、".join(item.get("name", item.get("status_id", "未知")) for item in statuses) or "正常"

    @staticmethod
    def _clamp(score: int) -> int:
        return max(0, min(10, int(score)))
