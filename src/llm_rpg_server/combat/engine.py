from __future__ import annotations

import random
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import PlayerRepository, PlayerService
from llm_rpg_server.shared.config import ContentProvider

from .models import CombatNarration
from .state import GameState


class CombatEngine:
    def __init__(
        self,
        catalog: Catalog,
        players: PlayerRepository,
        player_service: PlayerService,
        content: ContentProvider,
        llm: Any,
    ):
        self.catalog = catalog
        self.players = players
        self.player_service = player_service
        self.content = content
        self.llm = llm
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

    def _init_game(self, state: GameState) -> dict[str, Any]:
        profile = self.player_service.ensure(state["player_id"])
        result: dict[str, Any] = {
            "environment": random.choice(self.catalog.environments),
            "player_class": self.catalog.characters[profile.character_id],
            "turn_count": 1,
        }
        if state.get("game_mode", "PvE") == "PvE":
            ai_class = random.choice(list(self.catalog.characters.values()))
            armor = random.choice([value for key, value in self.catalog.armors.items() if key != "0"])
            ai_item_id = random.choice(list(self.catalog.items))
            result.update({
                "ai_class": ai_class,
                "ai_weapon": random.choice(list(self.catalog.weapons.values())),
                "ai_armor": armor,
                "ai_item": self.catalog.items[ai_item_id],
                "ai_item_id": ai_item_id,
                "ai_item_count": 1,
                "ai_hp": ai_class["hp"] + armor["hp_bonus"],
                "ai_mp": ai_class["mp"],
                "ai_status": self.content.text("combat.status_normal"),
            })
        return result

    def _ai_action(self, state: GameState) -> dict[str, Any]:
        if state.get("game_mode") == "PvP":
            return {}
        if state["ai_hp"] < state["ai_class"]["hp"] * 0.4 and state["ai_item_count"] > 0:
            return {"ai_action": self.action_from_key("i", state["ai_weapon"])}
        available = [skill for skill in state["ai_weapon"]["skills"] if state["ai_mp"] >= skill["cost"]]
        if available and random.random() > 0.3:
            return {"ai_action": self.action_from_key(random.choice(available)["id"], state["ai_weapon"])}
        return {"ai_action": self.action_from_key("0", state["ai_weapon"])}

    def _judge(self, state: GameState, config: RunnableConfig) -> dict[str, Any]:
        player_score = self._effectiveness(state["player_action"], state["player_status"], state["ai_action"])
        ai_score = self._effectiveness(state["ai_action"], state["ai_status"], state["player_action"])
        player_heal, player_mp_delta, player_damage = self._action_effects(
            "player", "ai", state["player_action"], player_score, state
        )
        ai_heal, ai_mp_delta, ai_damage = self._action_effects("ai", "player", state["ai_action"], ai_score, state)
        status_damage = int(self.catalog.rules["status_dmg_per_turn"])
        damaging_statuses = set(
            self.content.document("narratives/zh-CN.json")["texts"]["combat"]["status_damage"]
        )
        player_dot = -status_damage if state["player_status"] in damaging_statuses else 0
        ai_dot = -status_damage if state["ai_status"] in damaging_statuses else 0
        player_hp = max(0, min(
            state["player_hp"] + player_heal - ai_damage + player_dot,
            state["player_class"]["hp"] + state["player_armor"]["hp_bonus"],
        ))
        opponent_hp = max(0, min(
            state["ai_hp"] + ai_heal - player_damage + ai_dot,
            state["ai_class"]["hp"] + state["ai_armor"]["hp_bonus"],
        ))
        player_mp = max(0, state["player_mp"] + player_mp_delta)
        opponent_mp = max(0, state["ai_mp"] + ai_mp_delta)
        player_status = self._inflicted_status(state["ai_action"], ai_score)
        opponent_status = self._inflicted_status(state["player_action"], player_score)
        player_item_count = self._consume_item(state, "player", state["player_id"])
        if state.get("game_mode") == "PvP":
            ai_item_count = self._consume_item(state, "ai", state["p2_id"])
        else:
            ai_item_count = (
                state["ai_item_count"] - 1
                if state["ai_action"]["type"] == "item"
                else state["ai_item_count"]
            )
        player_name, opponent_name = self._names(state)
        narration = self._narrate(
            state,
            config,
            player_name,
            opponent_name,
            player_score,
            ai_score,
            player_damage,
            ai_damage,
        )
        log = self.content.text(
            "combat.round_log",
            turn=state["turn_count"],
            narration=narration,
            player_name=player_name,
            player_score=player_score,
            player_damage=player_damage,
            player_status=player_status,
            opponent_name=opponent_name,
            opponent_score=ai_score,
            opponent_damage=ai_damage,
            opponent_status=opponent_status,
            player_hp=player_hp,
            player_mp=player_mp,
            opponent_hp=opponent_hp,
            opponent_mp=opponent_mp,
        )
        return {
            "player_hp": player_hp,
            "player_mp": player_mp,
            "player_status": player_status,
            "player_item_count": player_item_count,
            "ai_hp": opponent_hp,
            "ai_mp": opponent_mp,
            "ai_status": opponent_status,
            "ai_item_count": ai_item_count,
            "messages": [AIMessage(content=log)],
            "turn_count": state["turn_count"] + 1,
        }

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
            message += self.content.text("combat.settlement.reward", gold=reward["gold"], loot=reward["loot"])
        return {"messages": [AIMessage(content=message)]}

    def _grant_reward(self, profile) -> dict[str, Any]:
        gold = random.randint(
            int(self.catalog.rules["min_gold_drop"]),
            int(self.catalog.rules["max_gold_drop"]),
        )
        profile.gold += gold
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
        return {"gold": gold, "loot": loot}

    def _narrate(
        self,
        state: GameState,
        config: RunnableConfig,
        player_name: str,
        opponent_name: str,
        player_score: int,
        opponent_score: int,
        player_damage: int,
        opponent_damage: int,
    ) -> str:
        definition = self.content.prompt("combat_judge")
        try:
            prompt = ChatPromptTemplate.from_messages([("system", definition.system), ("human", definition.user)])
            chain = prompt | self.llm.with_structured_output(CombatNarration)
            result = chain.invoke({
                "environment": state["environment"],
                "player_name": player_name,
                "player_hp": state["player_hp"],
                "player_status": state["player_status"],
                "player_weapon": state["player_weapon"]["name"],
                "player_armor": state["player_armor"]["name"],
                "player_action": state["player_action"]["name"],
                "player_score": player_score,
                "player_damage": player_damage,
                "opponent_name": opponent_name,
                "opponent_hp": state["ai_hp"],
                "opponent_status": state["ai_status"],
                "opponent_weapon": state["ai_weapon"]["name"],
                "opponent_armor": state["ai_armor"]["name"],
                "opponent_action": state["ai_action"]["name"],
                "opponent_score": opponent_score,
                "opponent_damage": opponent_damage,
            }, config=config)
            return result.combat_narration[:100]
        except Exception:
            return self.content.text("combat.fallback_narration")

    def _action_effects(
        self,
        actor: str,
        target: str,
        action: dict[str, Any],
        score: int,
        state: GameState,
    ) -> tuple[int, int, int]:
        hp_change = 0
        mp_change = -int(action["cost"])
        damage = 0
        if action["type"] == "item":
            item = state[f"{actor}_item"]
            if item is None:
                return 0, 0, 0
            if item["type"] == "heal_hp":
                hp_change += item["val"]
            elif item["type"] == "heal_mp":
                mp_change += item["val"]
            elif item["type"] == "heal_both":
                hp_change += item["val"]
                mp_change += item["val"]
            elif item["type"] == "dmg":
                damage = item["val"]
        elif action["type"] in {"attack", "skill"}:
            character = state[f"{actor}_class"]
            weapon = state[f"{actor}_weapon"]
            target_armor = state[f"{target}_armor"]
            coefficient = self.catalog.rules[
                "phys_attr_coeff" if weapon["type"] == "phys" else "magic_attr_coeff"
            ]
            attribute = character["str"] if weapon["type"] == "phys" else character["int"]
            raw_damage = (weapon["base_dmg"] + attribute * coefficient) * action.get("multiplier", 1.0)
            defense = target_armor["def_rate"] if weapon["type"] == "phys" else 0
            damage = int(raw_damage * (score / 5.0) * (1 - defense))
            if action.get("self_effect") == "life_steal":
                hp_change += int(damage * float(action.get("self_effect_ratio", 0)))
        return hp_change, mp_change, damage

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
            opponent_name = self.content.text("combat.ai_name")
        else:
            try:
                opponent_name = self.players.get(state["p2_id"]).name
            except KeyError:
                opponent_name = self.content.text("combat.opponent_default_name")
        return player_name, opponent_name

    def _effectiveness(self, action: dict[str, Any], actor_status: str, target_action: dict[str, Any]) -> int:
        statuses = self.content.document("narratives/zh-CN.json")["texts"]["combat"]["incapacitating_statuses"]
        if actor_status in statuses:
            return 0
        if target_action["type"] == "defense" and action["type"] in {"attack", "skill"}:
            return int(self.catalog.rules["defended_effectiveness"])
        return int(self.catalog.rules["normal_effectiveness"])

    def _inflicted_status(self, action: dict[str, Any], score: int) -> str:
        if score > 4 and action.get("status_effect"):
            return action["status_effect"]
        return self.content.text("combat.status_normal")

    def action_from_key(self, key: str, weapon: dict[str, Any]) -> dict[str, Any]:
        if key == "0":
            return {
                "id": "0",
                "name": self.content.text("combat.action.attack"),
                "cost": 0,
                "type": "attack",
                "multiplier": 1.0,
            }
        if key == "9":
            return {"id": "9", "name": self.content.text("combat.action.defense"), "cost": 0, "type": "defense"}
        if key == "i":
            return {"id": "item", "name": self.content.text("combat.action.item"), "cost": 0, "type": "item"}
        skill = next((item for item in weapon["skills"] if item["id"] == key), None)
        if skill is None:
            raise ValueError(self.content.text("errors.room.invalid_action"))
        return {
            "id": skill["id"],
            "name": self.content.text("combat.action.skill", skill_name=skill["name"]),
            "cost": skill["cost"],
            "type": "skill",
            "multiplier": skill["multiplier"],
            "status_effect": skill.get("status_effect"),
            "self_effect": skill.get("self_effect"),
            "self_effect_ratio": skill.get("self_effect_ratio"),
        }

    @staticmethod
    def _route_after_round(state: GameState) -> str:
        return "Settlement" if state["player_hp"] <= 0 or state["ai_hp"] <= 0 else "RoundStart"
