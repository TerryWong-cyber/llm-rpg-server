from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.npcs import NPCInteractionService
from llm_rpg_server.monsters import MonsterCatalog, MonsterDefinition
from llm_rpg_server.players import PlayerRepository
from llm_rpg_server.shared.config import ContentProvider
from llm_rpg_server.shared.observability import Observability

from .engine import CombatEngine
from .rooms import GameRoom, InMemoryRoomRepository


class CombatSessionService:
    def __init__(
        self,
        engine: CombatEngine,
        rooms: InMemoryRoomRepository,
        players: PlayerRepository,
        catalog: Catalog,
        npc_interactions: NPCInteractionService,
        content: ContentProvider,
        observability: Observability,
        monsters: MonsterCatalog | None = None,
    ):
        self.engine = engine
        self.rooms = rooms
        self.players = players
        self.catalog = catalog
        self.npc_interactions = npc_interactions
        self.content = content
        self.observability = observability
        self.monsters = monsters

    def create_room(self, player_id: str) -> GameRoom:
        self.players.get(player_id)
        return self.rooms.create(player_id)

    def join_room(self, room_id: str, player_id: str) -> GameRoom:
        room = self.rooms.get(room_id)
        self.players.get(player_id)
        with room.state_lock:
            if room.p1_id == player_id:
                raise ValueError(self.content.text("errors.room.same_player"))
            if room.p2_id or room.is_started:
                raise ValueError(self.content.text("errors.room.full"))
            room.p2_id = player_id
            room.mode = "PvP"
        return room

    def add_ai(self, room_id: str) -> GameRoom:
        room = self.rooms.get(room_id)
        with room.state_lock:
            if room.p2_id and room.p2_id != "AI_BOT":
                raise ValueError(self.content.text("errors.room.full"))
            room.p2_id = "AI_BOT"
            room.mode = "PvE"
            if not room.is_started:
                room.is_started = True
                self.engine.app.invoke({
                    "messages": [],
                    "player_id": room.p1_id,
                    "p2_id": room.p2_id,
                    "game_mode": room.mode,
                }, config=self.config(room))
        return room

    def start_pvp_if_ready(self, room: GameRoom) -> bool:
        with room.state_lock:
            if room.is_started or room.mode != "PvP" or room.p1_ws is None or room.p2_ws is None:
                return False
            room.is_started = True
            self.engine.app.invoke({
                "messages": [],
                "player_id": room.p1_id,
                "p2_id": room.p2_id,
                "game_mode": room.mode,
            }, config=self.config(room))
            return True

    def start_npc_combat(self, player_id: str, npc_id: str, trigger_id: str) -> GameRoom:
        self.players.get(player_id)
        npc = self.npc_interactions.repository.get_npc(npc_id)
        combat = npc.combat
        if combat is None:
            raise ValueError(self.content.text("errors.npc.no_combat"))
        if (
            combat.character_id not in self.catalog.characters
            or combat.weapon_id not in self.catalog.weapons
            or combat.armor_id not in self.catalog.armors
            or (combat.item_id is not None and combat.item_id not in self.catalog.items)
        ):
            raise ValueError(self.content.text("errors.npc.invalid_combat_profile"))
        self.npc_interactions.start_combat(npc_id, player_id, trigger_id)
        room = self.rooms.create_npc(player_id, npc_id, trigger_id)
        config = self.config(room)
        self.engine.app.invoke({
            "messages": [],
            "player_id": player_id,
            "p2_id": room.p2_id,
            "game_mode": "PvE",
            "reward_policy": "configured_opponent",
        }, config=config)
        enemy_class = self.catalog.characters[combat.character_id]
        enemy_armor = self.catalog.armors[combat.armor_id]
        self.engine.app.update_state(config, {
            "environment": combat.arena,
            "ai_class": enemy_class,
            "ai_weapon": self.catalog.weapons[combat.weapon_id],
            "ai_armor": enemy_armor,
            "ai_item": self.catalog.items.get(combat.item_id) if combat.item_id else None,
            "ai_item_id": combat.item_id,
            "ai_item_count": combat.item_count,
            "ai_hp": enemy_class["hp"] + enemy_armor["hp_bonus"],
            "ai_mp": enemy_class["mp"],
            "ai_status": self.content.text("combat.status_normal"),
            "reward_policy": "configured_opponent",
        })
        return room

    def start_monster_combat(
        self,
        player_id: str,
        monster_id: str,
        event_id: str,
    ) -> GameRoom:
        self.players.get(player_id)
        if self.monsters is None:
            raise ValueError(self.content.text("errors.npc.monster_unknown"))
        monster = self.monsters.get(monster_id)
        room = self.rooms.create_monster(player_id, monster_id, event_id)
        config = self.config(room)
        self.engine.app.invoke({
            "messages": [],
            "player_id": player_id,
            "p2_id": room.p2_id,
            "game_mode": "PvE",
            "reward_policy": "configured_opponent",
        }, config=config)
        equipment = monster.equipment
        enemy_class = {
            "id": monster.monster_id,
            "name": monster.name,
            "hp": monster.stats.hp,
            "mp": monster.stats.mp,
            "str": monster.stats.strength,
            "agi": monster.stats.agility,
            "int": monster.stats.intelligence,
            "desc": monster.description,
            "image_url": monster.image_url,
        }
        enemy_weapon = deepcopy(self.catalog.weapons[equipment.weapon_id])
        enemy_weapon["name"] = equipment.weapon_name
        enemy_armor = deepcopy(self.catalog.armors[equipment.armor_id])
        enemy_armor["name"] = equipment.armor_name
        self.engine.app.update_state(config, {
            "environment": monster.combat.arena,
            "ai_class": enemy_class,
            "ai_weapon": enemy_weapon,
            "ai_armor": enemy_armor,
            "ai_item": self.catalog.items.get(equipment.item_id) if equipment.item_id else None,
            "ai_item_id": equipment.item_id,
            "ai_item_count": equipment.item_count,
            "ai_hp": monster.stats.hp + int(enemy_armor["hp_bonus"]),
            "ai_mp": monster.stats.mp,
            "ai_status": self.content.text("combat.status_normal"),
            "reward_policy": "configured_opponent",
        })
        return room

    def submit_prep(self, room: GameRoom, player_id: str, payload: dict[str, str | None]) -> dict[str, Any] | None:
        is_p1 = self._member_side(room, player_id)
        self._validate_loadout(player_id, payload)
        if is_p1:
            room.p1_prep = payload
        else:
            room.p2_prep = payload
        if room.mode == "PvE":
            room.p2_prep = "AI_SKIP"
        if not room.p1_prep or not room.p2_prep:
            return None
        updates = self._loadout_state(room.p1_id, room.p1_prep, "player")
        if room.mode == "PvP":
            assert room.p2_id is not None and isinstance(room.p2_prep, dict)
            updates.update(self._loadout_state(room.p2_id, room.p2_prep, "ai"))
        config = self.config(room)
        self.engine.app.update_state(config, updates)
        for _ in self.engine.app.stream(None, config=config):
            pass
        room.p1_prep = None
        room.p2_prep = None
        return self.snapshot(room)

    def submit_action(self, room: GameRoom, player_id: str, action_key: str) -> dict[str, Any] | None:
        is_p1 = self._member_side(room, player_id)
        values = self.engine.app.get_state(self.config(room)).values
        prefix = "player" if is_p1 else "ai"
        action = self.engine.action_from_key(action_key, values[f"{prefix}_weapon"])
        self._validate_action(values, prefix, action)
        if is_p1:
            room.p1_act = action_key
        else:
            room.p2_act = action_key
        if room.mode == "PvE":
            room.p2_act = "AI_SKIP"
        if not room.p1_act or not room.p2_act:
            return None
        updates = {"player_action": self.engine.action_from_key(room.p1_act, values["player_weapon"])}
        self._validate_action(values, "player", updates["player_action"])
        if room.mode == "PvP":
            updates["ai_action"] = self.engine.action_from_key(room.p2_act, values["ai_weapon"])
            self._validate_action(values, "ai", updates["ai_action"])
        config = self.config(room)
        self.engine.app.update_state(config, updates)
        for _ in self.engine.app.stream(None, config=config):
            pass
        room.p1_act = None
        room.p2_act = None
        snapshot = self.snapshot(room)
        if snapshot["game_over"]:
            self._record_opponent_outcome(room)
            snapshot = self.snapshot(room)
            self.observability.flush()
        return snapshot

    def snapshot(self, room: GameRoom) -> dict[str, Any]:
        state_snapshot = self.engine.app.get_state(self.config(room))
        values = state_snapshot.values or {}
        next_node = state_snapshot.next[0] if state_snapshot.next else None
        player = self.players.get(room.p1_id)
        opponent = self.players.get(room.p2_id) if room.mode == "PvP" and room.p2_id else None
        npc_enemy = None
        if room.npc_id:
            view = self.npc_interactions.get_npc_view(room.npc_id, room.p1_id)
            npc_enemy = dict(view["npc"])
            npc_enemy["relationship"] = view["relationship"].model_dump(mode="json")
        monster_enemy = self.monsters.public_view(room.monster_id) if room.monster_id and self.monsters else None
        return {
            "room_id": room.room_id,
            "thread_id": room.thread_id,
            "next_node": next_node,
            "game_over": next_node is None,
            "state": {
                "environment": values.get("environment"),
                "turn_count": values.get("turn_count", 1),
                "game_mode": room.mode,
                "p1_id": room.p1_id,
                "p2_id": room.p2_id,
                "player_gold": player.gold,
                "player_inventory": player.inventory.model_dump(mode="json"),
                "player_class": values.get("player_class"),
                "player_weapon": values.get("player_weapon"),
                "player_armor": values.get("player_armor"),
                "player_item": values.get("player_item"),
                "player_item_id": values.get("player_item_id"),
                "player_item_count": values.get("player_item_count", 0),
                "player_hp": values.get("player_hp", 0),
                "player_mp": values.get("player_mp", 0),
                "player_status": values.get("player_status", self.content.text("combat.status_normal")),
                "ai_gold": opponent.gold if opponent else 0,
                "ai_inventory": opponent.inventory.model_dump(mode="json") if opponent else {},
                "ai_class": values.get("ai_class"),
                "ai_weapon": values.get("ai_weapon"),
                "ai_armor": values.get("ai_armor"),
                "ai_item": values.get("ai_item"),
                "ai_item_id": values.get("ai_item_id"),
                "ai_item_count": values.get("ai_item_count", 0),
                "ai_hp": values.get("ai_hp", 0),
                "ai_mp": values.get("ai_mp", 0),
                "ai_status": values.get("ai_status", self.content.text("combat.status_normal")),
            },
            "combat_log": values["messages"][-1].content if values.get("messages") else "",
            "npc_enemy": npc_enemy,
            "monster_enemy": monster_enemy,
            "reward_summary": room.reward_summary,
        }

    def config(self, room: GameRoom) -> dict[str, Any]:
        return self.observability.config(f"WS_Room_{room.room_id}_{room.mode}", room.thread_id)

    def _validate_loadout(self, player_id: str, payload: dict[str, str | None]) -> None:
        profile = self.players.get(player_id)
        weapon_id, armor_id, item_id = payload.get("weapon_id"), payload.get("armor_id"), payload.get("item_id")
        if weapon_id not in profile.inventory.weapons or weapon_id not in self.catalog.weapons:
            raise ValueError(self.content.text("errors.inventory.not_owned"))
        if armor_id not in profile.inventory.armors or armor_id not in self.catalog.armors:
            raise ValueError(self.content.text("errors.inventory.not_owned"))
        if item_id and (profile.inventory.items.get(item_id, 0) <= 0 or item_id not in self.catalog.items):
            raise ValueError(self.content.text("errors.inventory.not_owned"))

    def _loadout_state(self, player_id: str, payload: dict[str, str | None], prefix: str) -> dict[str, Any]:
        profile = self.players.get(player_id)
        character = self.catalog.characters[profile.character_id]
        weapon = self.catalog.weapons[payload["weapon_id"]]
        armor = self.catalog.armors[payload["armor_id"]]
        item_id = payload.get("item_id")
        return {
            f"{prefix}_class": character,
            f"{prefix}_weapon": weapon,
            f"{prefix}_armor": armor,
            f"{prefix}_hp": character["hp"] + armor["hp_bonus"],
            f"{prefix}_mp": character["mp"],
            f"{prefix}_status": self.content.text("combat.status_normal"),
            f"{prefix}_item": self.catalog.items[item_id] if item_id else None,
            f"{prefix}_item_id": item_id,
            f"{prefix}_item_count": profile.inventory.items.get(item_id, 0) if item_id else 0,
        }

    def _validate_action(self, values: dict[str, Any], prefix: str, action: dict[str, Any]) -> None:
        if action["cost"] > values[f"{prefix}_mp"]:
            raise ValueError(self.content.text("errors.room.insufficient_mp"))
        if action["type"] == "item" and values.get(f"{prefix}_item_count", 0) <= 0:
            raise ValueError(self.content.text("errors.room.item_unavailable"))

    def _member_side(self, room: GameRoom, player_id: str) -> bool:
        if player_id == room.p1_id:
            return True
        if player_id == room.p2_id:
            return False
        raise PermissionError(self.content.text("errors.room.invalid_member"))

    def _record_opponent_outcome(self, room: GameRoom) -> None:
        if room.opponent_outcome_recorded:
            return
        values = self.engine.app.get_state(self.config(room)).values
        player_hp, opponent_hp = values.get("player_hp", 0), values.get("ai_hp", 0)
        player_won = None if player_hp <= 0 and opponent_hp <= 0 else player_hp > 0 and opponent_hp <= 0
        if room.npc_id:
            self.npc_interactions.record_combat_outcome(room.npc_id, room.p1_id, player_won)
            room.npc_outcome_recorded = True
        elif room.monster_id and self.monsters and player_won is True:
            monster = self.monsters.get(room.monster_id)
            awarded, reward = self.players.update_once(
                room.p1_id,
                f"monster_reward:{room.thread_id}",
                lambda profile: self._grant_monster_reward(profile, monster, room.thread_id),
            )
            if awarded and reward:
                room.reward_summary = self._monster_reward_summary(reward)
        room.opponent_outcome_recorded = True

    def _grant_monster_reward(self, profile, monster: MonsterDefinition, seed: str) -> dict[str, Any]:
        rng = random.Random(f"{seed}:{monster.monster_id}")
        gold = rng.randint(monster.gold_min, monster.gold_max)
        profile.gold += gold
        drops: list[tuple[str, str, int]] = []
        for drop in monster.drops:
            if rng.random() > drop.chance:
                continue
            quantity = rng.randint(drop.minimum, drop.maximum)
            collection = profile.inventory.items if drop.item_type == "item" else profile.inventory.materials
            collection[drop.item_id] = collection.get(drop.item_id, 0) + quantity
            drops.append((drop.item_type, drop.item_id, quantity))
        return {"gold": gold, "drops": drops}

    def _monster_reward_summary(self, reward: dict[str, Any]) -> str:
        labels = []
        for item_type, item_id, quantity in reward["drops"]:
            definition = self.catalog.items[item_id] if item_type == "item" else self.catalog.resources[item_id]
            labels.append(self.content.text(
                "combat.settlement.monster_drop_item",
                name=definition["name"],
                quantity=quantity,
            ))
        loot = self.content.text("combat.settlement.monster_no_drop") if not labels else "、".join(labels)
        return self.content.text(
            "combat.settlement.monster_reward",
            gold=reward["gold"],
            loot=loot,
        )
