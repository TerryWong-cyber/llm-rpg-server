from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.monsters import MonsterCatalog, MonsterDefinition
from llm_rpg_server.npcs import NPCInteractionService
from llm_rpg_server.players import CharacterChronicleService, GrowthService, PlayerRepository, ResourceLifecycleService
from llm_rpg_server.players.models import PersistentCombatStatus
from llm_rpg_server.shared.config import ContentProvider
from llm_rpg_server.shared.observability import Observability
from llm_rpg_server.skills import SkillService

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
        growth: GrowthService,
        monsters: MonsterCatalog | None = None,
        resources: ResourceLifecycleService | None = None,
        skills: SkillService | None = None,
        chronicle: CharacterChronicleService | None = None,
    ):
        self.engine = engine
        self.rooms = rooms
        self.players = players
        self.catalog = catalog
        self.npc_interactions = npc_interactions
        self.content = content
        self.observability = observability
        self.growth = growth
        self.monsters = monsters
        self.resources = resources
        self.skills = skills
        self.chronicle = chronicle

    def create_room(self, player_id: str) -> GameRoom:
        self._require_combat_ready(player_id)
        return self.rooms.create(player_id)

    def join_room(self, room_id: str, player_id: str) -> GameRoom:
        room = self.rooms.get(room_id)
        self._require_combat_ready(player_id)
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
        should_prepare = False
        with room.state_lock:
            if room.p2_id and room.p2_id != "AI_BOT":
                raise ValueError(self.content.text("errors.room.full"))
            room.p2_id = "AI_BOT"
            room.mode = "PvE"
            if not room.is_started:
                room.is_started = True
                should_prepare = True
                self.engine.app.invoke({
                    "messages": [],
                    "player_id": room.p1_id,
                    "p2_id": room.p2_id,
                    "game_mode": room.mode,
                }, config=self.config(room))
        if should_prepare:
            self._prepare_pve_player(room)
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
        self._require_combat_ready(player_id)
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
        enemy_weapon = self.catalog.weapons[combat.weapon_id]
        enemy_stats = self.engine.rules.derive_stats(enemy_class, enemy_weapon, enemy_armor)
        enemy_context = self._environment_context_with_time(config, combat.arena)
        self.engine.app.update_state(config, {
            "environment": combat.arena,
            "environment_context": enemy_context,
            "ai_class": enemy_class,
            "ai_race": None,
            "ai_race_skills": [],
            "ai_weapon": enemy_weapon,
            "ai_armor": enemy_armor,
            "ai_item": self.catalog.items.get(combat.item_id) if combat.item_id else None,
            "ai_item_id": combat.item_id,
            "ai_item_count": combat.item_count,
            "ai_hp": enemy_stats.max_hp,
            "ai_mp": enemy_stats.max_mp,
            "ai_stamina": enemy_stats.max_stamina,
            "ai_status": self.content.text("combat.status_normal"),
            "ai_statuses": [],
            "ai_traits": [*npc.personality, *combat.tactics, npc.backstory.public_summary, npc.backstory.personal_goal],
            "ai_exposure": {},
            "ai_stats": enemy_stats.model_dump(mode="json"),
            "reward_policy": "configured_opponent",
        })
        self._prepare_pve_player(room)
        return room

    def start_monster_combat(
        self,
        player_id: str,
        monster_id: str,
        event_id: str,
    ) -> GameRoom:
        self._require_combat_ready(player_id)
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
            "wis": monster.stats.intelligence,
            "luck": 5,
            "traits": [*monster.tags, *monster.combat.tactics],
            "desc": monster.description,
            "image_url": monster.image_url,
        }
        enemy_weapon = deepcopy(self.catalog.weapons[equipment.weapon_id])
        enemy_weapon["name"] = equipment.weapon_name
        enemy_armor = deepcopy(self.catalog.armors[equipment.armor_id])
        enemy_armor["name"] = equipment.armor_name
        enemy_stats = self.engine.rules.derive_stats(enemy_class, enemy_weapon, enemy_armor)
        enemy_context = self._environment_context_with_time(config, monster.combat.arena)
        self.engine.app.update_state(config, {
            "environment": monster.combat.arena,
            "environment_context": enemy_context,
            "ai_class": enemy_class,
            "ai_race": None,
            "ai_race_skills": [],
            "ai_weapon": enemy_weapon,
            "ai_armor": enemy_armor,
            "ai_item": self.catalog.items.get(equipment.item_id) if equipment.item_id else None,
            "ai_item_id": equipment.item_id,
            "ai_item_count": equipment.item_count,
            "ai_hp": enemy_stats.max_hp,
            "ai_mp": enemy_stats.max_mp,
            "ai_stamina": enemy_stats.max_stamina,
            "ai_status": self.content.text("combat.status_normal"),
            "ai_statuses": [],
            "ai_traits": [*monster.tags, *monster.combat.tactics],
            "ai_exposure": {},
            "ai_stats": enemy_stats.model_dump(mode="json"),
            "reward_policy": "configured_opponent",
        })
        self._prepare_pve_player(room)
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
        config = self.config(room)
        current_values = self.engine.app.get_state(config).values
        environment_tags = current_values.get("environment_context", {}).get("tags", [])
        updates = self._loadout_state(
            room.p1_id,
            room.p1_prep,
            "player",
            environment_tags,
        )
        if room.mode == "PvP":
            assert room.p2_id is not None and isinstance(room.p2_prep, dict)
            updates.update(self._loadout_state(
                room.p2_id,
                room.p2_prep,
                "ai",
                environment_tags,
            ))
        self.engine.app.update_state(config, updates)
        for _ in self.engine.app.stream(None, config=config):
            pass
        room.p1_prep = None
        room.p2_prep = None
        return self.snapshot(room)

    def submit_action(
        self,
        room: GameRoom,
        player_id: str,
        action_key: str,
        item_id: str | None = None,
    ) -> dict[str, Any] | None:
        is_p1 = self._member_side(room, player_id)
        config = self.config(room)
        values = self.engine.app.get_state(config).values
        prefix = "player" if is_p1 else "ai"
        if action_key == "i":
            if not item_id:
                raise ValueError(self.content.text("errors.room.item_unavailable"))
            self.engine.app.update_state(config, self._combat_item_state(player_id, item_id, prefix))
            values = self.engine.app.get_state(config).values
        action = self.engine.action_from_key(
            action_key,
            values[f"{prefix}_weapon"],
            values.get(f"{prefix}_skills", []),
        )
        self._validate_action(values, prefix, action)
        if is_p1:
            room.p1_act = action_key
        else:
            room.p2_act = action_key
        if room.mode == "PvE":
            room.p2_act = "AI_SKIP"
        if not room.p1_act or not room.p2_act:
            return None
        updates = {"player_action": self.engine.action_from_key(
            room.p1_act,
            values["player_weapon"],
            values.get("player_skills", []),
        )}
        self._validate_action(values, "player", updates["player_action"])
        if room.mode == "PvP":
            updates["ai_action"] = self.engine.action_from_key(
                room.p2_act,
                values["ai_weapon"],
                values.get("ai_skills", []),
            )
            self._validate_action(values, "ai", updates["ai_action"])
        self.engine.app.update_state(config, updates)
        for _ in self.engine.app.stream(None, config=config):
            pass
        room.p1_act = None
        room.p2_act = None
        values = self.engine.app.get_state(config).values
        game_over = values.get("player_hp", 0) <= 0 or values.get("ai_hp", 0) <= 0
        if game_over:
            self._record_opponent_outcome(room)
        self._persist_combat_resources(room, values, game_over)
        if game_over:
            self.observability.flush()
        return self.snapshot(room)

    def _prepare_pve_player(self, room: GameRoom) -> None:
        if room.mode != "PvE":
            return
        profile = self.players.get(room.p1_id)
        payload = {
            "weapon_id": profile.equipped_weapon_id,
            "armor_id": profile.equipped_armor_id,
            "item_id": None,
        }
        if self.submit_prep(room, room.p1_id, payload) is None:
            raise RuntimeError("PvE equipped loadout did not advance combat")

    def _combat_item_state(self, player_id: str, item_id: str, prefix: str) -> dict[str, Any]:
        profile = self.players.get(player_id)
        definition = self.catalog.items.get(item_id)
        if definition is None or profile.inventory.items.get(item_id, 0) <= 0:
            raise ValueError(self.content.text("errors.inventory.not_owned"))
        if "combat" not in definition.get("use_contexts", []):
            raise ValueError(self.content.text("errors.inventory.context_forbidden"))
        return {
            f"{prefix}_item": definition,
            f"{prefix}_item_id": item_id,
            f"{prefix}_item_count": profile.inventory.items[item_id],
        }

    def snapshot(self, room: GameRoom) -> dict[str, Any]:
        state_snapshot = self.engine.app.get_state(self.config(room))
        values = state_snapshot.values or {}
        next_node = state_snapshot.next[0] if state_snapshot.next else None
        game_over = next_node is None
        player = self.players.get(room.p1_id)
        opponent = self.players.get(room.p2_id) if room.mode == "PvP" and room.p2_id else None
        player_persisted_statuses = [item.model_dump(mode="json") for item in player.combat_statuses]
        opponent_persisted_statuses = (
            [item.model_dump(mode="json") for item in opponent.combat_statuses] if opponent else []
        )
        player_status = (
            self.engine.status_label(player_persisted_statuses)
            if game_over
            else values.get("player_status", self.content.text("combat.status_normal"))
        )
        opponent_status = (
            self.engine.status_label(opponent_persisted_statuses)
            if game_over and opponent
            else values.get("ai_status", self.content.text("combat.status_normal"))
        )
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
            "game_over": game_over,
            "state": {
                "environment": values.get("environment"),
                "environment_context": values.get("environment_context", {}),
                "turn_count": values.get("turn_count", 1),
                "game_mode": room.mode,
                "p1_id": room.p1_id,
                "p2_id": room.p2_id,
                "player_gold": player.gold,
                "player_inventory": player.inventory.model_dump(mode="json"),
                "player_class": values.get("player_class"),
                "player_race": values.get("player_race"),
                "player_race_skills": values.get("player_race_skills", []),
                "player_skills": values.get("player_skills", []),
                "player_progression": self.growth.public_progress(player),
                "player_weapon": values.get("player_weapon"),
                "player_armor": values.get("player_armor"),
                "player_item": values.get("player_item"),
                "player_item_id": values.get("player_item_id"),
                "player_item_count": values.get("player_item_count", 0),
                "player_hp": player.current_hp if game_over else values.get("player_hp", player.current_hp),
                "player_max_hp": values.get("player_stats", {}).get("max_hp", player.max_hp),
                "player_mp": player.current_mp if game_over else values.get("player_mp", player.current_mp),
                "player_max_mp": values.get("player_stats", {}).get("max_mp", player.max_mp),
                "player_stamina": player.stamina if game_over else values.get("player_stamina", player.stamina),
                "player_max_stamina": values.get("player_stats", {}).get("max_stamina", player.max_stamina),
                "player_status": player_status,
                "player_statuses": player_persisted_statuses if game_over else values.get("player_statuses", []),
                "player_stats": values.get("player_stats", {}),
                "ai_gold": opponent.gold if opponent else 0,
                "ai_inventory": opponent.inventory.model_dump(mode="json") if opponent else {},
                "ai_class": values.get("ai_class"),
                "ai_race": values.get("ai_race"),
                "ai_race_skills": values.get("ai_race_skills", []),
                "ai_skills": values.get("ai_skills", []),
                "ai_progression": self.growth.public_progress(opponent) if opponent else None,
                "ai_weapon": values.get("ai_weapon"),
                "ai_armor": values.get("ai_armor"),
                "ai_item": values.get("ai_item"),
                "ai_item_id": values.get("ai_item_id"),
                "ai_item_count": values.get("ai_item_count", 0),
                "ai_hp": opponent.current_hp if game_over and opponent else values.get("ai_hp", 0),
                "ai_max_hp": values.get("ai_stats", {}).get("max_hp", opponent.max_hp if opponent else 0),
                "ai_mp": opponent.current_mp if game_over and opponent else values.get("ai_mp", 0),
                "ai_max_mp": values.get("ai_stats", {}).get("max_mp", opponent.max_mp if opponent else 0),
                "ai_stamina": opponent.stamina if game_over and opponent else values.get("ai_stamina", 0),
                "ai_max_stamina": values.get("ai_stats", {}).get(
                    "max_stamina", opponent.max_stamina if opponent else 0
                ),
                "ai_status": opponent_status,
                "ai_statuses": opponent_persisted_statuses if game_over and opponent else values.get("ai_statuses", []),
                "ai_stats": values.get("ai_stats", {}),
            },
            "combat_log": values["messages"][-1].content if values.get("messages") else "",
            "last_resolution": values.get("last_resolution", {}),
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
        if item_id and "combat" not in self.catalog.items[item_id].get("use_contexts", []):
            raise ValueError(self.content.text("errors.inventory.context_forbidden"))

    def _loadout_state(
        self,
        player_id: str,
        payload: dict[str, str | None],
        prefix: str,
        environment_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        original = self.players.get(player_id)
        character = self.engine.player_service.combat_character(original)
        race = self.catalog.races[original.race_id]
        weapon = self.catalog.weapons[payload["weapon_id"]]
        armor = self.catalog.armors[payload["armor_id"]]
        item_id = payload.get("item_id")
        stats = self.engine.rules.derive_stats(
            character,
            weapon,
            armor,
            [item.model_dump(mode="json") for item in original.combat_statuses],
            environment_tags,
        )
        with self.players.transaction(player_id) as profile:
            profile.equipped_weapon_id = str(payload["weapon_id"])
            profile.equipped_armor_id = str(payload["armor_id"])
            profile.equipped_item_id = str(item_id) if item_id else None
            profile.max_hp = stats.max_hp
            profile.max_mp = stats.max_mp
            profile.max_stamina = stats.max_stamina
            profile.current_hp = min(profile.current_hp, profile.max_hp)
            profile.current_mp = min(profile.current_mp, profile.max_mp)
            profile.stamina = min(profile.stamina, profile.max_stamina)
        profile = self.players.get(player_id)
        statuses = [item.model_dump(mode="json") for item in profile.combat_statuses]
        return {
            f"{prefix}_class": character,
            f"{prefix}_race": race,
            f"{prefix}_race_skills": list(race.get("exclusive_skills", [])),
            f"{prefix}_skills": self.skills.combat_skills(profile) if self.skills else [],
            f"{prefix}_weapon": weapon,
            f"{prefix}_armor": armor,
            f"{prefix}_hp": profile.current_hp,
            f"{prefix}_mp": profile.current_mp,
            f"{prefix}_stamina": profile.stamina,
            f"{prefix}_status": self.engine.status_label(statuses),
            f"{prefix}_statuses": statuses,
            f"{prefix}_traits": list(profile.psychological_traits),
            f"{prefix}_exposure": {},
            f"{prefix}_stats": stats.model_dump(mode="json"),
            f"{prefix}_item": self.catalog.items[item_id] if item_id else None,
            f"{prefix}_item_id": item_id,
            f"{prefix}_item_count": profile.inventory.items.get(item_id, 0) if item_id else 0,
        }

    def _validate_action(self, values: dict[str, Any], prefix: str, action: dict[str, Any]) -> None:
        if action.get("mp_cost", 0) > values[f"{prefix}_mp"]:
            raise ValueError(self.content.text("errors.room.insufficient_mp"))
        if action.get("stamina_cost", 0) > values[f"{prefix}_stamina"]:
            raise ValueError(self.content.text("errors.room.insufficient_stamina"))
        if action.get("hp_cost", 0) >= values[f"{prefix}_hp"]:
            raise ValueError(self.content.text("errors.skill.hp_condition"))
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
            if player_won is True:
                npc = self.npc_interactions.repository.get_npc(room.npc_id)
                threat = npc.combat.threat if npc.combat else 1
                amount = (
                    self.growth.rules.experience_rewards.npc_base
                    + threat * self.growth.rules.experience_rewards.per_threat
                )
                awarded, progress = self.growth.award_once(
                    room.p1_id,
                    amount,
                    f"npc_experience:{room.thread_id}",
                )
                if awarded and progress:
                    room.reward_summary = self._experience_summary(progress)
        elif room.monster_id and self.monsters and player_won is True:
            monster = self.monsters.get(room.monster_id)
            awarded, reward = self.players.update_once(
                room.p1_id,
                f"monster_reward:{room.thread_id}",
                lambda profile: self._grant_monster_reward(profile, monster, room.thread_id),
            )
            if awarded and reward:
                room.reward_summary = self._monster_reward_summary(reward)
        elif room.mode == "PvP" and player_won is not None:
            winner_id = room.p1_id if player_won else room.p2_id
            if winner_id:
                awarded, progress = self.growth.award_once(
                    winner_id,
                    self.growth.rules.experience_rewards.pvp_victory,
                    f"pvp_experience:{room.thread_id}",
                )
                if awarded and progress:
                    room.reward_summary = self._experience_summary(progress)
        self._record_combat_chronicle(room, player_won)
        room.opponent_outcome_recorded = True

    def _record_combat_chronicle(self, room: GameRoom, player_won: bool | None) -> None:
        if self.chronicle is None:
            return
        if room.npc_id:
            opponent_name = self.npc_interactions.repository.get_npc(room.npc_id).name
        elif room.monster_id and self.monsters:
            opponent_name = self.monsters.get(room.monster_id).name
        elif room.p2_id and room.mode == "PvP":
            opponent_name = self.players.get(room.p2_id).name
        else:
            opponent_name = self.chronicle.text("combat_unknown_opponent")

        def record(player_id: str, won: bool | None, opponent: str) -> None:
            outcome = "draw" if won is None else "victory" if won else "defeat"
            self.chronicle.record_player(
                player_id,
                "combat",
                self.chronicle.text(f"combat_{outcome}_title", opponent=opponent),
                self.chronicle.text(f"combat_{outcome}_description", opponent=opponent),
                emoji={"victory": "⚔", "defeat": "✕", "draw": "◇"}[outcome],
                source_id=f"combat:{room.thread_id}:{player_id}",
                details={
                    "thread_id": room.thread_id,
                    "outcome": outcome,
                    "npc_id": room.npc_id,
                    "monster_id": room.monster_id,
                },
            )

        record(room.p1_id, player_won, opponent_name)
        if room.mode == "PvP" and room.p2_id:
            record(room.p2_id, None if player_won is None else not player_won, self.players.get(room.p1_id).name)

    def _persist_combat_resources(self, room: GameRoom, values: dict[str, Any], game_over: bool) -> None:
        self._persist_side(room.p1_id, "player", values, game_over)
        if room.mode == "PvP" and room.p2_id:
            self._persist_side(room.p2_id, "ai", values, game_over)

    def _persist_side(self, player_id: str, prefix: str, values: dict[str, Any], game_over: bool) -> None:
        hp = int(values.get(f"{prefix}_hp", 0))
        statuses = [dict(item) for item in values.get(f"{prefix}_statuses", []) if item.get("persistent")]
        if game_over and hp <= 0:
            hp = 1
            weakness = self.engine.rules.statuses["weakness"]
            if not any(item.get("status_id") == "weakness" for item in statuses):
                statuses.append({
                    "status_id": "weakness",
                    "name": weakness["name"],
                    "source_id": "defeat",
                    "stacks": 1,
                    "potency": 1,
                    "remaining_turns": int(weakness["duration"]),
                    "persistent": True,
                    "tags": list(weakness["tags"]),
                })
        stats = values.get(f"{prefix}_stats", {})
        with self.players.transaction(player_id) as profile:
            profile.max_hp = max(1, int(stats.get("max_hp", profile.max_hp)))
            profile.max_mp = max(0, int(stats.get("max_mp", profile.max_mp)))
            profile.max_stamina = max(1, int(stats.get("max_stamina", profile.max_stamina)))
            profile.current_hp = min(profile.max_hp, max(0, hp))
            profile.current_mp = min(profile.max_mp, max(0, int(values.get(f"{prefix}_mp", 0))))
            profile.stamina = min(profile.max_stamina, max(0, int(values.get(f"{prefix}_stamina", 0))))
            profile.combat_statuses = [PersistentCombatStatus.model_validate(item) for item in statuses]

    def _require_combat_ready(self, player_id: str) -> None:
        profile = (
            self.resources.settle(player_id, interrupt_sleep=True)
            if self.resources is not None else self.players.get(player_id)
        )
        if profile.current_hp <= 0:
            raise ValueError(self.content.text("errors.room.incapacitated"))
        if (
            not profile.equipped_weapon_id
            or profile.equipped_weapon_id not in profile.inventory.weapons
            or profile.equipped_weapon_id not in self.catalog.weapons
            or not profile.equipped_armor_id
            or profile.equipped_armor_id not in profile.inventory.armors
            or profile.equipped_armor_id not in self.catalog.armors
        ):
            raise ValueError(self.content.text("errors.room.equipment_required"))

    def _grant_monster_reward(self, profile, monster: MonsterDefinition, seed: str) -> dict[str, Any]:
        rng = random.Random(f"{seed}:{monster.monster_id}")
        gold = rng.randint(monster.gold_min, monster.gold_max)
        profile.gold += gold
        progress = self.growth.apply_experience(
            profile,
            self.growth.rules.experience_rewards.monster_base
            + monster.combat.threat * self.growth.rules.experience_rewards.per_threat,
        )
        drops: list[tuple[str, str, int]] = []
        for drop in monster.drops:
            if rng.random() > drop.chance:
                continue
            quantity = rng.randint(drop.minimum, drop.maximum)
            collection = profile.inventory.items if drop.item_type == "item" else profile.inventory.materials
            collection[drop.item_id] = collection.get(drop.item_id, 0) + quantity
            drops.append((drop.item_type, drop.item_id, quantity))
        return {"gold": gold, "drops": drops, **progress}

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
            experience=reward["experience"],
            level_up=self._level_up_label(reward),
        )

    def _experience_summary(self, progress: dict[str, Any]) -> str:
        return self.content.text(
            "combat.settlement.experience",
            experience=progress["experience"],
            level_up=self._level_up_label(progress),
        )

    def _level_up_label(self, progress: dict[str, Any]) -> str:
        if int(progress.get("levels_gained", 0)) <= 0:
            return ""
        return self.content.text("combat.settlement.level_up", level=progress["level"])

    def _environment_context_with_time(
        self,
        config: dict[str, Any],
        description: str,
    ) -> dict[str, Any]:
        context = self.engine.rules.environment_context(description)
        current = self.engine.app.get_state(config).values.get("environment_context", {})
        temporal = {"day", "night", "dawn", "dusk", "sunlight", "dark"}
        context["tags"] = sorted(
            set(context.get("tags", []))
            | (set(current.get("tags", [])) & temporal)
        )
        return context
