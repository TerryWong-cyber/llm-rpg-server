from __future__ import annotations

import random
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import GrowthService, PlayerRepository, PlayerService
from llm_rpg_server.shared.config import ContentProvider

from .adjudication import EffectivenessJudge
from .hazards import EnvironmentHazardService
from .models import ActionOutcome, CombatNarration, DamagePacket, DerivedStats, EffectivenessAssessment
from .rules import CombatRulebook
from .state import GameState


class CombatEngine:
    def __init__(
        self,
        catalog: Catalog,
        players: PlayerRepository,
        player_service: PlayerService,
        growth: GrowthService,
        content: ContentProvider,
        llm: Any,
        world_clock: Any | None = None,
    ):
        self.catalog = catalog
        self.players = players
        self.player_service = player_service
        self.growth = growth
        self.content = content
        self.llm = llm
        self.world_clock = world_clock
        self.rules = CombatRulebook(content)
        self.hazards = EnvironmentHazardService(self.rules, content, llm)
        self.effectiveness = EffectivenessJudge(content, llm)
        self.app = self._compile()

    def _compile(self):
        workflow = StateGraph(GameState)
        workflow.add_node("Init", self._init_game)
        workflow.add_node("PlayerPrep", lambda state: {})
        workflow.add_node("RoundStart", lambda state: {})
        workflow.add_node("PlayerAction", lambda state: {})
        workflow.add_node("AIAction", self._ai_action)
        workflow.add_node("Judge", self._judge)
        workflow.add_node("Settlement", self._settlement)
        workflow.set_entry_point("Init")
        workflow.add_edge("Init", "PlayerPrep")
        workflow.add_edge("PlayerPrep", "RoundStart")
        workflow.add_edge("RoundStart", "PlayerAction")
        workflow.add_edge("RoundStart", "AIAction")
        workflow.add_edge(["PlayerAction", "AIAction"], "Judge")
        workflow.add_conditional_edges("Judge", self._route_after_round)
        workflow.add_edge("Settlement", END)
        return workflow.compile(checkpointer=MemorySaver(), interrupt_before=["PlayerPrep", "PlayerAction"])

    def _init_game(self, state: GameState, config: RunnableConfig) -> dict[str, Any]:
        profile = self.player_service.ensure(state["player_id"])
        environment = random.choice(self.catalog.environments)
        environment_context = self.rules.environment_context(environment)
        if self.world_clock is not None:
            period = self.world_clock.snapshot().period
            if period == "night":
                environment_context["tags"] = sorted({*environment_context["tags"], "night", "dark"})
            elif period == "day":
                environment_context["tags"] = sorted({*environment_context["tags"], "day", "sunlight"})
            else:
                environment_context["tags"] = sorted({*environment_context["tags"], period})
        thread_id = str(config.get("configurable", {}).get("thread_id", "combat"))
        player_class = self.player_service.combat_character(profile)
        player_race = self.catalog.races[profile.race_id]
        result: dict[str, Any] = {
            "environment": environment,
            "environment_context": environment_context,
            "combat_seed": thread_id,
            "player_class": player_class,
            "player_race": player_race,
            "player_race_skills": list(player_race.get("exclusive_skills", [])),
            "player_hp": profile.current_hp,
            "player_mp": profile.current_mp,
            "player_stamina": profile.stamina,
            "player_statuses": [item.model_dump(mode="json") for item in profile.combat_statuses],
            "player_traits": list(profile.psychological_traits),
            "player_status": self.status_label([item.model_dump(mode="json") for item in profile.combat_statuses]),
            "player_exposure": {},
            "turn_count": 1,
            "last_resolution": {},
        }
        if state.get("game_mode", "PvE") == "PvE":
            ai_class = random.choice(list(self.catalog.characters.values()))
            armor = random.choice([value for key, value in self.catalog.armors.items() if key != "0"])
            weapon = random.choice(list(self.catalog.weapons.values()))
            ai_item_id = random.choice(list(self.catalog.items))
            ai_stats = self.rules.derive_stats(ai_class, weapon, armor)
            result.update({
                "ai_class": ai_class,
                "ai_race": None,
                "ai_race_skills": [],
                "ai_weapon": weapon,
                "ai_armor": armor,
                "ai_item": self.catalog.items[ai_item_id],
                "ai_item_id": ai_item_id,
                "ai_item_count": 1,
                "ai_hp": ai_stats.max_hp,
                "ai_mp": ai_stats.max_mp,
                "ai_stamina": ai_stats.max_stamina,
                "ai_status": self.content.text("combat.status_normal"),
                "ai_statuses": [],
                "ai_traits": list(ai_class.get("traits", [])),
                "ai_exposure": {},
                "ai_stats": ai_stats.model_dump(mode="json"),
            })
        return result

    def _ai_action(self, state: GameState) -> dict[str, Any]:
        if state.get("game_mode") == "PvP":
            return {}
        if state["ai_hp"] < self._max_hp(state, "ai") * 0.4 and state["ai_item_count"] > 0:
            return {"ai_action": self.action_from_key("i", state["ai_weapon"])}
        available = []
        for skill in state["ai_weapon"].get("skills", []):
            action = self.action_from_key(
                str(skill["id"]),
                state["ai_weapon"],
                state.get("ai_race_skills", []),
            )
            if state["ai_mp"] >= action["mp_cost"] and state["ai_stamina"] >= action["stamina_cost"]:
                available.append(action)
        if available and random.random() > 0.3:
            return {"ai_action": random.choice(available)}
        return {"ai_action": self.action_from_key("0", state["ai_weapon"], state.get("ai_race_skills", []))}

    def _judge(self, state: GameState, config: RunnableConfig) -> dict[str, Any]:
        judgement = self.effectiveness.evaluate(state, config)
        player_rng = self._rng(state, "player")
        opponent_rng = self._rng(state, "opponent")
        player_stats = self.rules.derive_stats(
            state["player_class"],
            state["player_weapon"],
            state["player_armor"],
            state.get("player_statuses", []),
            state.get("environment_context", {}).get("tags", []),
        )
        opponent_stats = self.rules.derive_stats(
            state["ai_class"],
            state["ai_weapon"],
            state["ai_armor"],
            state.get("ai_statuses", []),
            state.get("environment_context", {}).get("tags", []),
        )
        player_outcome = self._resolve_action(
            state, "player", "ai", state["player_action"], judgement.player, player_stats, opponent_stats, player_rng
        )
        opponent_outcome = self._resolve_action(
            state, "ai", "player", state["ai_action"], judgement.opponent, opponent_stats, player_stats, opponent_rng
        )

        player_statuses, player_dot = self.rules.tick_statuses(
            state.get("player_statuses", []), player_stats, self._rng(state, "player-status")
        )
        opponent_statuses, opponent_dot = self.rules.tick_statuses(
            state.get("ai_statuses", []), opponent_stats, self._rng(state, "opponent-status")
        )
        if player_outcome.clear_negative_statuses:
            player_statuses = [item for item in player_statuses if "negative" not in item.get("tags", [])]
        if opponent_outcome.clear_negative_statuses:
            opponent_statuses = [item for item in opponent_statuses if "negative" not in item.get("tags", [])]
        environment_tags = state.get("environment_context", {}).get("tags", [])
        if player_outcome.damage.hit:
            opponent_statuses = self.rules.apply_status(
                opponent_statuses,
                state["player_action"].get("status_effect"),
                state["player_id"],
                opponent_stats,
                player_rng,
                environment_tags,
                state["player_action"].get("status_chance"),
            )
        if opponent_outcome.damage.hit:
            player_statuses = self.rules.apply_status(
                player_statuses,
                state["ai_action"].get("status_effect"),
                state.get("p2_id", "opponent"),
                player_stats,
                opponent_rng,
                environment_tags,
                state["ai_action"].get("status_chance"),
            )

        player_exposure, player_statuses, player_environment_damage = self.rules.environment_tick(
            state.get("environment_context", {}),
            state.get("player_exposure", {}),
            player_stats,
            player_statuses,
            self._rng(state, "player-environment"),
            "environment",
        )
        opponent_exposure, opponent_statuses, opponent_environment_damage = self.rules.environment_tick(
            state.get("environment_context", {}),
            state.get("ai_exposure", {}),
            opponent_stats,
            opponent_statuses,
            self._rng(state, "opponent-environment"),
            "environment",
        )

        player_incoming = opponent_outcome.damage.total + player_dot.total + player_environment_damage.total
        opponent_incoming = player_outcome.damage.total + opponent_dot.total + opponent_environment_damage.total
        player_hp = max(0, min(
            player_stats.max_hp,
            state["player_hp"] + player_outcome.hp_restore - player_incoming,
        ))
        opponent_hp = max(0, min(
            opponent_stats.max_hp,
            state["ai_hp"] + opponent_outcome.hp_restore - opponent_incoming,
        ))
        player_mp = max(0, min(player_stats.max_mp, state["player_mp"] + player_outcome.mp_delta))
        opponent_mp = max(0, min(opponent_stats.max_mp, state["ai_mp"] + opponent_outcome.mp_delta))
        player_stamina = max(
            0, min(player_stats.max_stamina, state["player_stamina"] + player_outcome.stamina_delta)
        )
        opponent_stamina = max(
            0, min(opponent_stats.max_stamina, state["ai_stamina"] + opponent_outcome.stamina_delta)
        )
        player_item_count = self._consume_item(state, "player", state["player_id"])
        if state.get("game_mode") == "PvP":
            opponent_item_count = self._consume_item(state, "ai", state["p2_id"])
        else:
            opponent_item_count = state["ai_item_count"] - (1 if state["ai_action"]["type"] == "item" else 0)

        player_name, opponent_name = self._names(state)
        narration = self._narrate(
            state,
            config,
            player_name,
            opponent_name,
            judgement.player,
            judgement.opponent,
            player_outcome.damage.total,
            opponent_outcome.damage.total,
        )
        player_status_label = self.status_label(player_statuses)
        opponent_status_label = self.status_label(opponent_statuses)
        log = self.content.text(
            "combat.round_log",
            turn=state["turn_count"],
            narration=narration,
            player_name=player_name,
            player_score=judgement.player.score,
            player_reason=judgement.player.reason,
            player_damage=player_outcome.damage.total,
            player_status=player_status_label,
            opponent_name=opponent_name,
            opponent_score=judgement.opponent.score,
            opponent_reason=judgement.opponent.reason,
            opponent_damage=opponent_outcome.damage.total,
            opponent_status=opponent_status_label,
            player_hp=player_hp,
            player_mp=player_mp,
            player_stamina=player_stamina,
            opponent_hp=opponent_hp,
            opponent_mp=opponent_mp,
            opponent_stamina=opponent_stamina,
        )
        resolution = {
            "turn": state["turn_count"],
            "player": {
                "effectiveness": judgement.player.model_dump(mode="json"),
                "damage": player_outcome.damage.model_dump(mode="json"),
                "status_damage_received": player_dot.model_dump(mode="json"),
                "environment_damage_received": player_environment_damage.model_dump(mode="json"),
            },
            "opponent": {
                "effectiveness": judgement.opponent.model_dump(mode="json"),
                "damage": opponent_outcome.damage.model_dump(mode="json"),
                "status_damage_received": opponent_dot.model_dump(mode="json"),
                "environment_damage_received": opponent_environment_damage.model_dump(mode="json"),
            },
        }
        return {
            "player_hp": player_hp,
            "player_mp": player_mp,
            "player_stamina": player_stamina,
            "player_status": player_status_label,
            "player_statuses": player_statuses,
            "player_exposure": player_exposure,
            "player_stats": player_stats.model_dump(mode="json"),
            "player_item_count": player_item_count,
            "ai_hp": opponent_hp,
            "ai_mp": opponent_mp,
            "ai_stamina": opponent_stamina,
            "ai_status": opponent_status_label,
            "ai_statuses": opponent_statuses,
            "ai_exposure": opponent_exposure,
            "ai_stats": opponent_stats.model_dump(mode="json"),
            "ai_item_count": max(0, opponent_item_count),
            "last_resolution": resolution,
            "messages": [AIMessage(content=log)],
            "turn_count": state["turn_count"] + 1,
        }

    def _resolve_action(
        self,
        state: GameState,
        actor: str,
        target: str,
        action: dict[str, Any],
        assessment: EffectivenessAssessment,
        actor_stats: DerivedStats,
        target_stats: DerivedStats,
        rng: random.Random,
    ) -> ActionOutcome:
        if self.rules.is_incapacitated(state.get(f"{actor}_statuses", [])):
            return ActionOutcome()
        outcome = ActionOutcome(
            mp_delta=-int(action.get("mp_cost", 0)),
            stamina_delta=-int(action.get("stamina_cost", 0)),
        )
        if action["type"] == "item":
            item = state.get(f"{actor}_item")
            if item is None:
                return ActionOutcome()
            if item["type"] in {"heal_hp", "heal_both"}:
                outcome.hp_restore += int(item["val"])
            if item["type"] in {"heal_mp", "heal_both"}:
                outcome.mp_delta += int(item["val"])
            if item["type"] == "dmg":
                packet = DamagePacket(true=float(item["val"]))
                outcome.damage = self.rules.resolve_damage(
                    packet, actor_stats, target_stats, assessment.score, rng, can_miss=True, can_crit=False
                )
            outcome.clear_negative_statuses = bool(item.get("clear_negative_statuses", False))
        elif action["type"] in {"attack", "skill"}:
            packet = self.rules.attack_packet(
                state[f"{actor}_weapon"], actor_stats, float(action.get("multiplier", 1))
            )
            outcome.damage = self.rules.resolve_damage(
                packet, actor_stats, target_stats, assessment.score, rng
            )
            if action.get("self_effect") == "life_steal":
                outcome.hp_restore += int(
                    outcome.damage.total * float(action.get("self_effect_ratio", 0))
                )
            passive_ratio = float(
                state.get(f"{actor}_class", {}).get("passives", {}).get("life_steal", 0)
            )
            if passive_ratio > 0:
                outcome.hp_restore += int(outcome.damage.total * passive_ratio)
        return outcome

    def _settlement(self, state: GameState, config: RunnableConfig) -> dict[str, Any]:
        if state["player_hp"] <= 0 and state["ai_hp"] <= 0:
            return {"messages": [AIMessage(content=self.content.text("combat.settlement.draw"))]}
        if state["player_hp"] <= 0:
            winner = state.get("p2_id") if state.get("game_mode") == "PvP" else self.content.text("combat.strong_enemy")
            return {"messages": [AIMessage(content=self.content.text("combat.settlement.defeat", winner=winner))]}
        message = self.content.text("combat.settlement.victory")
        if state.get("game_mode") == "PvP":
            return {"messages": [AIMessage(content=message + self.content.text("combat.settlement.pvp"))]}
        if state.get("reward_policy") == "configured_opponent":
            return {"messages": [AIMessage(content=message)]}
        thread_id = config.get("configurable", {}).get("thread_id", "unknown")
        awarded, reward = self.players.update_once(
            state["player_id"],
            f"combat_reward:{thread_id}",
            self._grant_reward,
        )
        if awarded and reward:
            message += self.content.text(
                "combat.settlement.reward",
                gold=reward["gold"],
                loot=reward["loot"],
                experience=reward["experience"],
            )
        return {"messages": [AIMessage(content=message)]}

    def _grant_reward(self, profile) -> dict[str, Any]:
        gold = random.randint(int(self.catalog.rules["min_gold_drop"]), int(self.catalog.rules["max_gold_drop"]))
        profile.gold += gold
        progress = self.growth.apply_experience(
            profile,
            self.growth.rules.experience_rewards.random_pve,
        )
        loot_type = random.choice(["weapon", "armor", "item"])
        loot = ""
        if loot_type == "weapon":
            available = [item_id for item_id in self.catalog.weapons if item_id not in profile.inventory.weapons]
            if available:
                item_id = random.choice(available)
                profile.inventory.weapons.append(item_id)
                loot = self.content.text("combat.settlement.weapon_loot", name=self.catalog.weapons[item_id]["name"])
            else:
                loot_type = "item"
        if loot_type == "armor":
            available = [item_id for item_id in self.catalog.armors if item_id not in profile.inventory.armors]
            if available:
                item_id = random.choice(available)
                profile.inventory.armors.append(item_id)
                loot = self.content.text("combat.settlement.armor_loot", name=self.catalog.armors[item_id]["name"])
            else:
                loot_type = "item"
        if loot_type == "item":
            item_id = random.choice(list(self.catalog.items))
            profile.inventory.items[item_id] = profile.inventory.items.get(item_id, 0) + 1
            loot = self.content.text("combat.settlement.item_loot", name=self.catalog.items[item_id]["name"])
        return {"gold": gold, "loot": loot, **progress}

    def _narrate(
        self,
        state: GameState,
        config: RunnableConfig,
        player_name: str,
        opponent_name: str,
        player_assessment: EffectivenessAssessment,
        opponent_assessment: EffectivenessAssessment,
        player_damage: int,
        opponent_damage: int,
    ) -> str:
        definition = self.content.prompt("combat_judge")
        try:
            prompt = ChatPromptTemplate.from_messages([("system", definition.system), ("human", definition.user)])
            result = (prompt | self.llm.with_structured_output(CombatNarration)).invoke({
                "environment": state["environment"],
                "player_name": player_name,
                "player_status": self.status_label(state.get("player_statuses", [])),
                "player_weapon": state["player_weapon"]["name"],
                "player_armor": state["player_armor"]["name"],
                "player_action": state["player_action"]["name"],
                "player_score": player_assessment.score,
                "player_reason": player_assessment.reason,
                "player_damage": player_damage,
                "opponent_name": opponent_name,
                "opponent_status": self.status_label(state.get("ai_statuses", [])),
                "opponent_weapon": state["ai_weapon"]["name"],
                "opponent_armor": state["ai_armor"]["name"],
                "opponent_action": state["ai_action"]["name"],
                "opponent_score": opponent_assessment.score,
                "opponent_reason": opponent_assessment.reason,
                "opponent_damage": opponent_damage,
            }, config=config)
            return result.combat_narration[:100]
        except Exception:
            return self.content.text("combat.fallback_narration")

    def _consume_item(self, state: GameState, prefix: str, player_id: str) -> int:
        count = state[f"{prefix}_item_count"]
        if state[f"{prefix}_action"]["type"] != "item" or count <= 0:
            return count
        item_id = state.get(f"{prefix}_item_id")
        if item_id:
            with self.players.transaction(player_id) as profile:
                profile.inventory.items[item_id] = max(0, profile.inventory.items.get(item_id, 0) - 1)
        return count - 1

    def _names(self, state: GameState) -> tuple[str, str]:
        try:
            player_name = self.players.get(state["player_id"]).name
        except KeyError:
            player_name = self.content.text("combat.player_default_name")
        if state.get("game_mode") == "PvE":
            opponent_name = state.get("ai_class", {}).get("name") or self.content.text("combat.ai_name")
        else:
            try:
                opponent_name = self.players.get(state["p2_id"]).name
            except KeyError:
                opponent_name = self.content.text("combat.opponent_default_name")
        return player_name, opponent_name

    def action_from_key(
        self,
        key: str,
        weapon: dict[str, Any],
        race_skills: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if key == "0":
            return {
                "id": "0",
                "name": self.content.text("combat.action.attack"),
                "description": weapon.get("desc", "常规攻击"),
                "cost": 0,
                "mp_cost": 0,
                "stamina_cost": 0,
                "type": "attack",
                "multiplier": 1.0,
            }
        if key == "9":
            return {
                "id": "9",
                "name": self.content.text("combat.action.defense"),
                "description": "放弃进攻并集中应对对手的招式。",
                "cost": 0,
                "mp_cost": 0,
                "stamina_cost": 0,
                "type": "defense",
            }
        if key == "i":
            return {
                "id": "item",
                "name": self.content.text("combat.action.item"),
                "description": "使用当前携带的战斗物品。",
                "cost": 0,
                "mp_cost": 0,
                "stamina_cost": 0,
                "type": "item",
            }
        skill = next(
            (
                item
                for item in [*weapon.get("skills", []), *(race_skills or [])]
                if str(item["id"]) == str(key)
            ),
            None,
        )
        if skill is None:
            raise ValueError(self.content.text("errors.room.invalid_action"))
        legacy_cost = int(skill.get("cost", 0))
        return {
            "id": str(skill["id"]),
            "name": self.content.text("combat.action.skill", skill_name=skill["name"]),
            "description": skill.get("desc", ""),
            "cost": legacy_cost,
            "mp_cost": int(skill.get("mp_cost", legacy_cost)),
            "stamina_cost": 0,
            "type": "skill",
            "multiplier": float(skill.get("multiplier", 1)),
            "status_effect": skill.get("status_effect"),
            "status_chance": skill.get("status_chance"),
            "self_effect": skill.get("self_effect"),
            "self_effect_ratio": skill.get("self_effect_ratio"),
        }

    def _rng(self, state: GameState, namespace: str) -> random.Random:
        return random.Random(f"{state.get('combat_seed', 'combat')}:{state['turn_count']}:{namespace}")

    def _max_hp(self, state: GameState, prefix: str) -> int:
        stats = state.get(f"{prefix}_stats") or {}
        return int(stats.get("max_hp", state[f"{prefix}_class"].get("hp", 1)))

    def status_label(self, statuses: list[dict[str, Any]]) -> str:
        if not statuses:
            return self.content.text("combat.status_normal")
        return "、".join(
            f"{item.get('name', item.get('status_id', '异常'))}×{item.get('stacks', 1)}"
            f"({item.get('remaining_turns', 0)}回合)"
            for item in statuses
        )

    @staticmethod
    def _route_after_round(state: GameState) -> str:
        return "Settlement" if state["player_hp"] <= 0 or state["ai_hp"] <= 0 else "RoundStart"
