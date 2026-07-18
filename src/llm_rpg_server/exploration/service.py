from __future__ import annotations

import hashlib
import random
from typing import Any, Literal, Protocol

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import PlayerRepository
from llm_rpg_server.players.models import PlayerProfile, WorldEventLogEntry, WorldEventState
from llm_rpg_server.shared.config import ContentProvider
from llm_rpg_server.world.time import WorldClock, WorldTimeSnapshot

from .models import (
    ActionAvailability,
    EncounterResult,
    MapCell,
    MapInstance,
    MapScale,
    MapTemplate,
    MapTransition,
    WorldEventResult,
)

Direction = Literal["up", "down", "left", "right"]


class EncounterResolver(Protocol):
    def resolve(
        self,
        player_id: str,
        map_instance: MapInstance,
        cell: MapCell,
        trigger: str,
    ) -> EncounterResult | None: ...


class EventParticipantResolver(Protocol):
    def public_participant(self, rule: dict[str, Any]) -> dict[str, Any] | None: ...


class ExplorationService:
    def __init__(
        self,
        players: PlayerRepository,
        catalog: Catalog,
        content: ContentProvider,
        encounter_resolver: EncounterResolver | None = None,
        clock: WorldClock | None = None,
    ):
        self.players = players
        self.catalog = catalog
        self.content = content
        self.encounter_resolver = encounter_resolver
        payload = content.document("maps/world.json")
        self.schema_version = payload["schema_version"]
        self.worlds = payload["worlds"]
        self.regions = payload["regions"]
        self.scale_defaults = payload["scale_defaults"]
        self.gather_quantity = payload.get("gather_quantity", {"minimum": 1, "maximum": 1})
        self.terrains = payload["terrains"]
        self.world_grid = payload.get("world_grid", {"width": 1, "height": 1})
        self.action_rules = payload.get("action_rules", {})
        self.event_rules = payload.get("events", [])
        self.clock = clock or WorldClock(payload["time"])
        self.templates = {
            template_id: self._template(definition)
            for template_id, definition in payload["templates"].items()
        }
        self.default_template_id = payload["default_template_id"]
        self.template_by_region = {item.region_id: item for item in self.templates.values()}
        self.region_by_position = {
            (int(item["world_x"]), int(item["world_y"])): region_id
            for region_id, item in self.regions.items()
        }
        self._latest_events: dict[str, WorldEventResult | None] = {}
        self.event_participant_resolver: EventParticipantResolver | None = None
        self._validate_configuration()

    def set_encounter_resolver(self, resolver: EncounterResolver) -> None:
        self.encounter_resolver = resolver

    def set_event_participant_resolver(self, resolver: EventParticipantResolver) -> None:
        self.event_participant_resolver = resolver

    def list_templates(self) -> list[dict[str, Any]]:
        return [template.model_dump(mode="json") for template in self.templates.values()]

    def time_snapshot(self) -> WorldTimeSnapshot:
        return self.clock.snapshot()

    def world_overview(self) -> dict[str, Any]:
        return {
            "width": int(self.world_grid["width"]),
            "height": int(self.world_grid["height"]),
            "regions": self.regions,
        }

    def pop_latest_event(self, player_id: str) -> WorldEventResult | None:
        return self._latest_events.pop(player_id, None)

    def enter(
        self,
        player_id: str,
        template_id: str | None = None,
        *,
        refresh: bool = False,
        seed: int | None = None,
    ) -> tuple[MapInstance, EncounterResult | None]:
        with self.players.transaction(player_id) as profile:
            existing = MapInstance.model_validate(profile.current_map) if profile.current_map else None
            birthplace = self.catalog.races[profile.race_id]["birthplace"]
            chosen_template = template_id or (
                existing.template_id if existing is not None and not refresh else birthplace["template_id"]
            )
            if chosen_template not in self.templates:
                raise ValueError(self.content.text("errors.map.invalid_template"))
            template = self.templates[chosen_template]
            if seed is not None:
                profile.world_seed = seed
                if refresh:
                    profile.world_maps.clear()
            elif profile.world_seed is None:
                profile.world_seed = random.SystemRandom().randint(0, 2**31 - 1)
            current = existing
            if current is None or refresh or current.template_id != chosen_template:
                current = self._generate(template, self._region_seed(profile.world_seed, template))
                if chosen_template == birthplace["template_id"]:
                    self._place_at_birthplace(current, birthplace)
            else:
                self._hydrate_cells(current)
            current.cells[current.current_cell_id].explored = True
            cell = current.cells[current.current_cell_id]
            encounter = self._resolve(player_id, current, cell, "on_enter_map")
            self._latest_events[player_id] = self._resolve_event(profile, current, cell, "on_enter_map")
            self._save_current(profile, current)
            return current, encounter

    def move(self, player_id: str, cell_id: int) -> tuple[MapInstance, EncounterResult | None]:
        with self.players.transaction(player_id) as profile:
            current = self._current(profile)
            cell = self._cell(current, cell_id)
            if not self._adjacent(current, current.current_cell_id, cell_id):
                raise ValueError(self.content.text("errors.map.invalid_cell"))
            encounter = self._move_within(profile, player_id, current, cell)
            return current, encounter

    def move_direction(
        self,
        player_id: str,
        direction: Direction,
    ) -> tuple[MapInstance, EncounterResult | None, MapTransition | None]:
        delta = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
        with self.players.transaction(player_id) as profile:
            current = self._current(profile)
            source = current.cells[current.current_cell_id]
            self._require_no_blocking_event(profile, current, source)
            next_x, next_y = source.x + delta[0], source.y + delta[1]
            if 0 <= next_x < current.width and 0 <= next_y < current.height:
                target = current.cells[next_y * current.width + next_x]
                encounter = self._move_within(profile, player_id, current, target)
                return current, encounter, None
            target_world_x = current.world_x + delta[0]
            target_world_y = current.world_y + delta[1]
            target_region_id = self.region_by_position.get((target_world_x, target_world_y))
            if target_region_id is None:
                raise ValueError(self.content.text("errors.map.world_edge"))
            if profile.world_seed is None:
                profile.world_seed = current.seed
            profile.world_maps[self._map_key(current)] = current.model_dump(mode="json")
            target_template = self.template_by_region[target_region_id]
            target_key = f"{target_template.world_id}:{target_region_id}"
            if target_key in profile.world_maps:
                destination = MapInstance.model_validate(profile.world_maps[target_key])
                self._hydrate_cells(destination)
            else:
                destination = self._generate(
                    target_template,
                    self._region_seed(profile.world_seed, target_template),
                )
            entry = self._edge_entry(destination, direction, source)
            self._spend(profile, entry.movement_cost)
            destination.current_cell_id = entry.cell_id
            entry.explored = True
            encounter = self._resolve(player_id, destination, entry, "on_enter_cell")
            self._latest_events[player_id] = self._resolve_event(
                profile, destination, entry, "on_enter_cell"
            )
            self._save_current(profile, destination)
            return destination, encounter, MapTransition(
                from_region_id=current.region_id,
                to_region_id=destination.region_id,
                direction=direction,
            )

    def gather(
        self,
        player_id: str,
        cell_id: int,
    ) -> tuple[dict[str, int], MapInstance, EncounterResult | None]:
        with self.players.transaction(player_id) as profile:
            current = self._current(profile)
            cell = self._cell(current, cell_id)
            if cell.cell_id != current.current_cell_id:
                raise ValueError(self.content.text("errors.map.not_current_cell"))
            if cell.gathered:
                raise ValueError(self.content.text("errors.map.depleted"))
            terrain = self.terrains[cell.terrain_id]
            if not cell.gatherable:
                raise ValueError(self.content.text("errors.map.not_gatherable"))
            now = self.clock.snapshot()
            rules = [
                rule for rule in terrain.get("gather_rules", [])
                if self._gather_rule_matches(rule, current, now)
            ]
            if not rules:
                raise ValueError(self.content.text("errors.map.no_resources_now"))
            self._spend(profile, int(self.action_rules.get("gather_cost", 8)))
            rng = random.Random(f"{current.seed}:gather:{cell_id}")
            loot: dict[str, int] = {}
            for rule in rules:
                if rng.random() <= float(rule.get("chance", 1.0)):
                    minimum = int(rule.get("minimum", self.gather_quantity["minimum"]))
                    maximum = int(rule.get("maximum", self.gather_quantity["maximum"]))
                    quantity = rng.randint(minimum, maximum)
                    resource_id = rule["resource_id"]
                    loot[resource_id] = loot.get(resource_id, 0) + quantity
            if not loot:
                fallback = rules[0]
                resource_id = fallback["resource_id"]
                loot[resource_id] = int(
                    fallback.get("minimum", self.gather_quantity["minimum"])
                )
            cell.gathered = True
            cell.explored = True
            for resource_id, quantity in loot.items():
                profile.inventory.materials[resource_id] = (
                    profile.inventory.materials.get(resource_id, 0) + quantity
                )
            encounter = self._resolve(player_id, current, cell, "on_gather")
            self._latest_events[player_id] = self._resolve_event(
                profile, current, cell, "on_gather"
            )
            self._save_current(profile, current)
            return loot, current, encounter

    def eat(self, player_id: str, item_id: str) -> PlayerProfile:
        definition = self.catalog.items.get(item_id)
        restore = int(definition.get("stamina_restore", 0)) if definition else 0
        if definition is None or restore <= 0:
            raise ValueError(self.content.text("errors.map.not_food"))
        with self.players.transaction(player_id) as profile:
            if profile.inventory.items.get(item_id, 0) <= 0:
                raise ValueError(self.content.text("errors.inventory.not_owned"))
            if profile.stamina >= profile.max_stamina:
                raise ValueError(self.content.text("errors.map.stamina_full"))
            profile.inventory.items[item_id] -= 1
            if profile.inventory.items[item_id] == 0:
                del profile.inventory.items[item_id]
            profile.stamina = min(profile.max_stamina, profile.stamina + restore)
        return self.players.get(player_id)

    def camp(self, player_id: str) -> PlayerProfile:
        with self.players.transaction(player_id) as profile:
            current = self._current(profile)
            cell = current.cells[current.current_cell_id]
            if not cell.campable:
                raise ValueError(self.content.text("errors.map.cannot_camp"))
            now = self.clock.snapshot()
            game_day = now.total_game_hours // 24
            if profile.last_camped_game_day == game_day:
                raise ValueError(self.content.text("errors.map.camp_cooldown"))
            if (
                profile.stamina >= profile.max_stamina
                and profile.current_hp >= profile.max_hp
                and profile.current_mp >= profile.max_mp
            ):
                raise ValueError(self.content.text("errors.map.stamina_full"))
            profile.stamina = min(
                profile.max_stamina,
                profile.stamina + int(self.action_rules.get("camp_restore", 60)),
            )
            profile.current_hp = min(
                profile.max_hp,
                profile.current_hp + max(1, round(profile.max_hp * float(self.action_rules.get("camp_hp_ratio", 0.4)))),
            )
            profile.current_mp = min(
                profile.max_mp,
                profile.current_mp + max(1, round(profile.max_mp * float(self.action_rules.get("camp_mp_ratio", 0.4)))),
            )
            profile.last_camped_game_day = game_day
        return self.players.get(player_id)

    def rest_at_inn(self, player_id: str) -> PlayerProfile:
        with self.players.transaction(player_id) as profile:
            current = self._current(profile)
            cell = current.cells[current.current_cell_id]
            terrain = self.terrains.get(cell.terrain_id, {})
            if "inn" not in terrain.get("interactions", []):
                raise ValueError(self.content.text("errors.map.inn_unavailable"))
            profile.current_hp = profile.max_hp
            profile.current_mp = profile.max_mp
            profile.stamina = profile.max_stamina
            profile.combat_statuses = []
        return self.players.get(player_id)

    def actions(self, player_id: str) -> dict[str, ActionAvailability]:
        profile = self.players.get(player_id)
        if not profile.current_map:
            return {}
        current = MapInstance.model_validate(profile.current_map)
        self._hydrate_cells(current)
        cell = current.cells[current.current_cell_id]
        now = self.clock.snapshot()
        terrain = self.terrains[cell.terrain_id]
        gather_cost = int(self.action_rules.get("gather_cost", 8))
        has_timed_gather = any(
            self._gather_rule_matches(rule, current, now)
            for rule in terrain.get("gather_rules", [])
        )
        shop_open, shop_reason = self.can_trade(player_id, profile=profile, now=now)
        camp_day = now.total_game_hours // 24
        can_camp = cell.campable and profile.last_camped_game_day != camp_day
        can_use_inn = "inn" in terrain.get("interactions", [])
        camp_reason = ""
        if not cell.campable:
            camp_reason = self.content.text("errors.map.cannot_camp")
        elif profile.last_camped_game_day == camp_day:
            camp_reason = self.content.text("errors.map.camp_cooldown")
        return {
            "gather": ActionAvailability(
                available=(
                    cell.gatherable and not cell.gathered and has_timed_gather
                    and profile.stamina >= gather_cost
                ),
                reason=self._gather_reason(cell, has_timed_gather, profile.stamina, gather_cost),
                cost=gather_cost,
            ),
            "camp": ActionAvailability(
                available=can_camp and (
                    profile.stamina < profile.max_stamina
                    or profile.current_hp < profile.max_hp
                    or profile.current_mp < profile.max_mp
                ),
                reason=camp_reason,
            ),
            "shop": ActionAvailability(available=shop_open, reason=shop_reason),
            "inn": ActionAvailability(
                available=can_use_inn and (
                    profile.current_hp < profile.max_hp
                    or profile.current_mp < profile.max_mp
                    or profile.stamina < profile.max_stamina
                    or bool(profile.combat_statuses)
                ),
                reason="" if can_use_inn else self.content.text("errors.map.inn_unavailable"),
            ),
            "eat": ActionAvailability(
                available=any(
                    quantity > 0 and int(self.catalog.items.get(item_id, {}).get("stamina_restore", 0)) > 0
                    for item_id, quantity in profile.inventory.items.items()
                ) and profile.stamina < profile.max_stamina,
            ),
        }

    def can_trade(
        self,
        player_id: str,
        *,
        profile: PlayerProfile | None = None,
        now: WorldTimeSnapshot | None = None,
    ) -> tuple[bool, str]:
        value = profile or self.players.get(player_id)
        if not value.current_map:
            return False, self.content.text("errors.shop.not_in_settlement")
        current = MapInstance.model_validate(value.current_map)
        cell = current.cells[current.current_cell_id]
        terrain = self.terrains.get(cell.terrain_id, {})
        if "shop" not in terrain.get("interactions", []):
            return False, self.content.text("errors.shop.not_in_settlement")
        time_value = now or self.clock.snapshot()
        if not self.clock.matches(terrain.get("shop_conditions"), time_value):
            return False, self.content.text("errors.shop.closed")
        return True, ""

    def require_trade_access(self, player_id: str) -> None:
        allowed, reason = self.can_trade(player_id)
        if not allowed:
            raise ValueError(reason)

    def require_stamina(self, player_id: str, action: str) -> None:
        cost = int(self.action_rules.get(f"{action}_cost", 0))
        if self.players.get(player_id).stamina < cost:
            raise ValueError(self.content.text("errors.map.stamina"))

    def spend_stamina(self, player_id: str, action: str) -> None:
        cost = int(self.action_rules.get(f"{action}_cost", 0))
        with self.players.transaction(player_id) as profile:
            self._spend(profile, cost)

    def _template(self, definition: dict[str, Any]) -> MapTemplate:
        payload = dict(definition)
        scale = MapScale(payload["scale"])
        if scale is not MapScale.CUSTOM:
            dimensions = self.scale_defaults[scale.value]
            payload.setdefault("width", dimensions["width"])
            payload.setdefault("height", dimensions["height"])
        return MapTemplate.model_validate(payload)

    def _generate(self, template: MapTemplate, actual_seed: int) -> MapInstance:
        rng = random.Random(actual_seed)
        assert template.width is not None and template.height is not None
        total = template.width * template.height
        if template.terrain_counts:
            if sum(template.terrain_counts.values()) != total:
                raise ValueError(f"Map template {template.template_id} terrain counts must total {total}")
            terrain_by_cell = self._clustered_layout(template, rng)
        else:
            terrain_ids = list(template.terrain_weights)
            weights = list(template.terrain_weights.values())
            terrain_by_cell = {
                cell_id: rng.choices(terrain_ids, weights=weights, k=1)[0]
                for cell_id in range(total)
            }
        self._apply_landmark_terrains(terrain_by_cell, template.landmark_terrains)
        cells = [
            self._new_cell(
                cell_id,
                template.width,
                terrain_by_cell[cell_id],
                template.landmarks.get(cell_id),
            )
            for cell_id in range(total)
        ]
        start = self._nearest_passable(cells, template.width, template.width // 2, template.height // 2)
        start.explored = True
        region = self.regions[template.region_id]
        return MapInstance(
            map_id=f"map_{template.region_id}_{actual_seed:08x}",
            template_id=template.template_id,
            world_id=template.world_id,
            region_id=template.region_id,
            scale=template.scale,
            width=template.width,
            height=template.height,
            seed=actual_seed,
            config_version=self.schema_version,
            world_x=int(region["world_x"]),
            world_y=int(region["world_y"]),
            world_width=int(self.world_grid["width"]),
            world_height=int(self.world_grid["height"]),
            current_cell_id=start.cell_id,
            cells=cells,
        )

    def _place_at_birthplace(
        self,
        current: MapInstance,
        birthplace: dict[str, Any],
    ) -> None:
        cell_id = int(birthplace["cell_id"])
        if cell_id < 0 or cell_id >= len(current.cells):
            raise ValueError(f"Race birthplace cell is outside map: {cell_id}")
        cell = current.cells[cell_id]
        terrain = self.terrains.get(cell.terrain_id, {})
        interactions = set(terrain.get("interactions", []))
        if not {"shop", "inn"}.issubset(interactions):
            raise ValueError(
                f"Race birthplace {birthplace['settlement_name']} must provide shop and inn"
            )
        for map_cell in current.cells:
            map_cell.explored = False
        current.current_cell_id = cell_id
        cell.explored = True

    def _clustered_layout(self, template: MapTemplate, rng: random.Random) -> dict[int, str]:
        assert template.width is not None and template.height is not None
        counts = dict(template.terrain_counts)
        primary = template.primary_terrain_id or max(counts, key=counts.get)
        unassigned = set(range(template.width * template.height))
        result: dict[int, str] = {}
        terrain_order = sorted(
            (terrain_id for terrain_id in counts if terrain_id != primary),
            key=lambda terrain_id: (
                int(self.terrains[terrain_id].get("placement_priority", 0)),
                -counts[terrain_id],
            ),
            reverse=True,
        )
        for terrain_id in terrain_order:
            remaining = counts[terrain_id]
            clusters = min(remaining, max(1, int(self.terrains[terrain_id].get("clusters", 2))))
            cluster_sizes = [remaining // clusters] * clusters
            for index in range(remaining % clusters):
                cluster_sizes[index] += 1
            for cluster_size in cluster_sizes:
                self._grow_cluster(
                    result, unassigned, terrain_id, cluster_size,
                    template.width, template.height, rng,
                )
        if len(unassigned) != counts[primary]:
            raise ValueError(f"Terrain placement drifted for {template.template_id}")
        for cell_id in unassigned:
            result[cell_id] = primary
        return result

    @staticmethod
    def _apply_landmark_terrains(
        terrain_by_cell: dict[int, str],
        landmark_terrains: dict[int, str],
    ) -> None:
        """Force landmark terrain by swapping cells so configured counts stay exact."""
        for landmark_cell_id, required_terrain_id in landmark_terrains.items():
            previous_terrain_id = terrain_by_cell[landmark_cell_id]
            if previous_terrain_id == required_terrain_id:
                continue
            swap_cell_id = next(
                cell_id for cell_id, terrain_id in sorted(terrain_by_cell.items())
                if terrain_id == required_terrain_id and cell_id not in landmark_terrains
            )
            terrain_by_cell[landmark_cell_id] = required_terrain_id
            terrain_by_cell[swap_cell_id] = previous_terrain_id

    @staticmethod
    def _grow_cluster(
        result: dict[int, str],
        unassigned: set[int],
        terrain_id: str,
        count: int,
        width: int,
        height: int,
        rng: random.Random,
    ) -> None:
        frontier: set[int] = set()
        for _ in range(count):
            candidates = sorted(frontier & unassigned)
            cell_id = rng.choice(candidates if candidates else sorted(unassigned))
            unassigned.remove(cell_id)
            frontier.discard(cell_id)
            result[cell_id] = terrain_id
            x, y = cell_id % width, cell_id // width
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    neighbor = ny * width + nx
                    if neighbor in unassigned:
                        frontier.add(neighbor)

    def _new_cell(self, cell_id: int, width: int, terrain_id: str, landmark_id: str | None) -> MapCell:
        terrain = self.terrains[terrain_id]
        return MapCell(
            cell_id=cell_id,
            x=cell_id % width,
            y=cell_id // width,
            terrain_id=terrain_id,
            landmark_id=landmark_id,
            passable=terrain.get("passable", True),
            terrain_category=terrain.get("category", "ordinary"),
            tags=list(terrain.get("tags", [])),
            gatherable=bool(terrain.get("gatherable", False)),
            campable=bool(terrain.get("campable", False)),
            movement_cost=int(terrain.get("movement_cost", 1)),
            npc_chance_multiplier=float(terrain.get("npc_chance_multiplier", 1.0)),
            interaction_ids=list(terrain.get("interactions", [])),
        )

    def _hydrate_cells(self, current: MapInstance) -> None:
        region = self.regions.get(current.region_id, {})
        current.world_x = int(region.get("world_x", current.world_x))
        current.world_y = int(region.get("world_y", current.world_y))
        current.world_width = int(self.world_grid["width"])
        current.world_height = int(self.world_grid["height"])
        for cell in current.cells:
            terrain = self.terrains[cell.terrain_id]
            cell.passable = bool(terrain.get("passable", True))
            cell.terrain_category = terrain.get("category", "ordinary")
            cell.tags = list(terrain.get("tags", []))
            cell.gatherable = bool(terrain.get("gatherable", False))
            cell.campable = bool(terrain.get("campable", False))
            cell.movement_cost = int(terrain.get("movement_cost", 1))
            cell.npc_chance_multiplier = float(terrain.get("npc_chance_multiplier", 1.0))
            cell.interaction_ids = list(terrain.get("interactions", []))

    def _move_within(
        self,
        profile: PlayerProfile,
        player_id: str,
        current: MapInstance,
        cell: MapCell,
    ) -> EncounterResult | None:
        self._require_no_blocking_event(
            profile,
            current,
            current.cells[current.current_cell_id],
        )
        if not cell.passable:
            raise ValueError(self.content.text("errors.map.impassable"))
        self._spend(profile, cell.movement_cost)
        current.current_cell_id = cell.cell_id
        cell.explored = True
        encounter = self._resolve(player_id, current, cell, "on_enter_cell")
        self._latest_events[player_id] = self._resolve_event(
            profile, current, cell, "on_enter_cell"
        )
        self._save_current(profile, current)
        return encounter

    def _resolve(self, player_id: str, current: MapInstance, cell: MapCell, trigger: str) -> EncounterResult | None:
        if self.encounter_resolver is None:
            return None
        return self.encounter_resolver.resolve(player_id, current, cell, trigger)

    def _resolve_event(
        self,
        profile: PlayerProfile,
        current: MapInstance,
        cell: MapCell,
        trigger: str,
    ) -> WorldEventResult | None:
        now = self.clock.snapshot()
        game_day = now.total_game_hours // 24
        for rule in sorted(self.event_rules, key=lambda item: item.get("priority", 0), reverse=True):
            if rule.get("trigger") != trigger:
                continue
            if rule.get("region_ids") and current.region_id not in rule["region_ids"]:
                continue
            if rule.get("terrain_ids") and cell.terrain_id not in rule["terrain_ids"]:
                continue
            if rule.get("terrain_tags") and not set(rule["terrain_tags"]) & set(cell.tags):
                continue

            scope_key = self._event_scope_key(rule, current, cell)
            state = profile.world_event_states.setdefault(
                scope_key,
                WorldEventState(event_id=rule["event_id"], scope_key=scope_key),
            )
            if state.active:
                if not self._event_is_bound_to_cell(state, current, cell):
                    continue
                persistence = rule.get("persistence", "none")
                if persistence == "until_resolved" or self._event_conditions_match(rule, current, now):
                    return self._event_result(
                        rule,
                        cell,
                        state,
                        trigger,
                        result_state="active",
                        title=rule.get("active_title", rule["title"]),
                        description=rule.get("active_description", rule["description"]),
                    )
                state.active = False
                state.ended_game_day = game_day
                title = rule.get("expiration_title", f"{rule['title']}已经结束")
                description = rule.get(
                    "expiration_description", "这里已经恢复平静，先前的事件不再存在。"
                )
                self._append_event_log(
                    profile, current, cell, rule, now, "expired", title, description
                )
                return self._event_result(
                    rule,
                    cell,
                    state,
                    trigger,
                    result_state="expired",
                    title=title,
                    description=description,
                    include_actions=False,
                )

            if not self._event_conditions_match(rule, current, now):
                continue
            max_triggers = rule.get("max_triggers")
            if max_triggers is not None and state.trigger_count >= int(max_triggers):
                continue
            cooldown_days = max(1, int(rule.get("cooldown_days", 1)))
            if (
                state.last_checked_game_day is not None
                and game_day - state.last_checked_game_day < cooldown_days
            ):
                continue
            state.last_checked_game_day = game_day
            rng = random.Random(
                f"{current.seed}:event:{scope_key}:{rule['event_id']}:{game_day}:{state.trigger_count}"
            )
            if rng.random() > float(rule.get("chance", 1.0)):
                continue
            state.trigger_count += 1
            state.active = rule.get("persistence", "none") in {
                "while_conditions_match",
                "until_resolved",
            }
            state.active_world_id = current.world_id if state.active else None
            state.active_region_id = current.region_id if state.active else None
            state.active_map_id = current.map_id if state.active else None
            state.active_cell_id = cell.cell_id if state.active else None
            state.active_since_game_day = game_day if state.active else None
            state.ended_game_day = None
            if rule["event_id"] not in cell.triggered_event_ids:
                cell.triggered_event_ids.append(rule["event_id"])
            self._append_event_log(
                profile, current, cell, rule, now, "triggered", rule["title"], rule["description"]
            )
            return self._event_result(rule, cell, state, trigger, result_state="triggered")
        return None

    def event_action(
        self,
        player_id: str,
        event_id: str,
        action_id: str,
    ) -> tuple[MapInstance, WorldEventResult]:
        with self.players.transaction(player_id) as profile:
            current = self._current(profile)
            cell = current.cells[current.current_cell_id]
            rule, action, state = self._event_action_context(
                profile, current, cell, event_id, action_id
            )

            now = self.clock.snapshot()
            title = action.get("result_title", rule["title"])
            description = action.get("result_description", rule["description"])
            state.last_action_id = action_id
            if action.get("resolution", "keep_active") == "end_event":
                state.active = False
                state.ended_game_day = now.total_game_hours // 24
            self._append_event_log(
                profile, current, cell, rule, now, "action", title, description
            )
            result = self._event_result(
                rule,
                cell,
                state,
                "event_action",
                result_state="action",
                title=title,
                description=description,
                include_actions=False,
            )
            self._latest_events[player_id] = result
            self._save_current(profile, current)
            return current, result

    def validate_event_action(
        self,
        player_id: str,
        event_id: str,
        action_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        profile = self.players.get(player_id)
        current = self._current(profile)
        cell = current.cells[current.current_cell_id]
        rule, action, _ = self._event_action_context(
            profile, current, cell, event_id, action_id
        )
        return rule, action

    def active_event_ids(
        self,
        profile: PlayerProfile,
        current: MapInstance,
        cell: MapCell,
    ) -> list[str]:
        return sorted({
            state.event_id
            for state in profile.world_event_states.values()
            if state.active and self._event_is_bound_to_cell(state, current, cell)
        })

    @staticmethod
    def _event_is_bound_to_cell(
        state: WorldEventState,
        current: MapInstance,
        cell: MapCell,
    ) -> bool:
        return (
            state.active_world_id == current.world_id
            and state.active_region_id == current.region_id
            and state.active_map_id == current.map_id
            and state.active_cell_id == cell.cell_id
        )

    @staticmethod
    def _event_scope_key(
        rule: dict[str, Any],
        current: MapInstance,
        cell: MapCell,
    ) -> str:
        event_id = rule["event_id"]
        scope = rule.get("trigger_scope", "cell")
        if scope == "world":
            return f"{event_id}:world:{current.world_id}"
        if scope == "region":
            return f"{event_id}:region:{current.world_id}:{current.region_id}"
        return f"{event_id}:cell:{current.map_id}:{cell.cell_id}"

    @staticmethod
    def _event_actions(rule: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "action_id": action["action_id"],
                "label": action["label"],
                "style": action.get("style", "quiet"),
                "kind": action.get("kind", "narrative"),
                "forced": bool(action.get("forced", False)),
            }
            for action in rule.get("actions", [])
        ]

    def _event_conditions_match(
        self,
        rule: dict[str, Any],
        current: MapInstance,
        now: WorldTimeSnapshot,
    ) -> bool:
        conditions = rule.get("conditions") or {}
        if not self.clock.matches(conditions, now):
            return False
        regions = conditions.get("regions", [])
        return not regions or current.region_id in regions

    def _event_result(
        self,
        rule: dict[str, Any],
        cell: MapCell,
        state: WorldEventState,
        trigger: str,
        *,
        result_state: Literal["triggered", "active", "expired", "action"],
        title: str | None = None,
        description: str | None = None,
        include_actions: bool = True,
    ) -> WorldEventResult:
        return WorldEventResult(
            event_id=rule["event_id"],
            kind=rule.get("kind", "flavor"),
            title=title or rule["title"],
            description=description or rule["description"],
            emoji=rule.get("emoji", "✦"),
            trigger=trigger,
            state=result_state,
            actions=self._event_actions(rule) if include_actions else [],
            trigger_scope=rule.get("trigger_scope", "cell"),
            trigger_count=state.trigger_count,
            max_triggers=rule.get("max_triggers"),
            cell_id=cell.cell_id,
            participant=(
                self.event_participant_resolver.public_participant(rule)
                if self.event_participant_resolver else None
            ),
            blocks_movement=bool(rule.get("blocks_movement", False)),
        )

    def _event_action_context(
        self,
        profile: PlayerProfile,
        current: MapInstance,
        cell: MapCell,
        event_id: str,
        action_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], WorldEventState]:
        rule = next((item for item in self.event_rules if item["event_id"] == event_id), None)
        if rule is None:
            raise ValueError(self.content.text("errors.map.event_unknown"))
        scope_key = self._event_scope_key(rule, current, cell)
        state = profile.world_event_states.get(scope_key)
        if state is None or not state.active or not self._event_is_bound_to_cell(state, current, cell):
            raise ValueError(self.content.text("errors.map.event_not_active"))
        action = next(
            (item for item in rule.get("actions", []) if item["action_id"] == action_id),
            None,
        )
        if action is None:
            raise ValueError(self.content.text("errors.map.event_action_invalid"))
        return rule, action, state

    def _require_no_blocking_event(
        self,
        profile: PlayerProfile,
        current: MapInstance,
        cell: MapCell,
    ) -> None:
        active_ids = {
            state.event_id
            for state in profile.world_event_states.values()
            if state.active and self._event_is_bound_to_cell(state, current, cell)
        }
        if any(
            rule["event_id"] in active_ids and rule.get("blocks_movement", False)
            for rule in self.event_rules
        ):
            raise ValueError(self.content.text("errors.map.event_blocks_movement"))

    @staticmethod
    def _append_event_log(
        profile: PlayerProfile,
        current: MapInstance,
        cell: MapCell,
        rule: dict[str, Any],
        now: WorldTimeSnapshot,
        phase: Literal["triggered", "action", "expired"],
        title: str,
        description: str,
    ) -> None:
        profile.world_event_log.append(WorldEventLogEntry(
            log_id=(
                f"{rule['event_id']}:{phase}:{now.total_game_hours}:"
                f"{current.map_id}:{cell.cell_id}:{len(profile.world_event_log)}"
            ),
            event_id=rule["event_id"],
            phase=phase,
            title=title,
            description=description,
            emoji=rule.get("emoji", "✦"),
            kind=rule.get("kind", "flavor"),
            world_id=current.world_id,
            region_id=current.region_id,
            map_id=current.map_id,
            cell_id=cell.cell_id,
            game_hour=now.total_game_hours,
            year=now.year,
            month=now.month,
            day=now.day,
            hour=now.hour,
            season=now.season,
        ))
        if len(profile.world_event_log) > 200:
            del profile.world_event_log[:-200]

    def _edge_entry(self, destination: MapInstance, direction: Direction, source: MapCell) -> MapCell:
        if direction == "right":
            candidates = [cell for cell in destination.cells if cell.x == 0]
            target_axis = source.y
            key = lambda cell: abs(cell.y - target_axis)
        elif direction == "left":
            candidates = [cell for cell in destination.cells if cell.x == destination.width - 1]
            target_axis = source.y
            key = lambda cell: abs(cell.y - target_axis)
        elif direction == "down":
            candidates = [cell for cell in destination.cells if cell.y == 0]
            target_axis = source.x
            key = lambda cell: abs(cell.x - target_axis)
        else:
            candidates = [cell for cell in destination.cells if cell.y == destination.height - 1]
            target_axis = source.x
            key = lambda cell: abs(cell.x - target_axis)
        passable = [cell for cell in candidates if cell.passable]
        if not passable:
            raise ValueError(self.content.text("errors.map.no_border_entry"))
        return min(passable, key=key)

    @staticmethod
    def _nearest_passable(cells: list[MapCell], width: int, x: int, y: int) -> MapCell:
        return min(
            (cell for cell in cells if cell.passable),
            key=lambda cell: abs(cell.x - x) + abs(cell.y - y),
        )

    @staticmethod
    def _region_seed(world_seed: int, template: MapTemplate) -> int:
        digest = hashlib.blake2b(
            f"{world_seed}:{template.world_id}:{template.region_id}".encode(), digest_size=8
        ).digest()
        return int.from_bytes(digest, "big") & 0x7FFFFFFF

    @staticmethod
    def _map_key(current: MapInstance) -> str:
        return f"{current.world_id}:{current.region_id}"

    def _save_current(self, profile: PlayerProfile, current: MapInstance) -> None:
        payload = current.model_dump(mode="json")
        profile.current_map = payload
        profile.world_maps[self._map_key(current)] = payload

    def _current(self, profile: PlayerProfile) -> MapInstance:
        if not profile.current_map:
            raise RuntimeError(self.content.text("errors.map.not_entered"))
        current = MapInstance.model_validate(profile.current_map)
        self._hydrate_cells(current)
        return current

    def _cell(self, current: MapInstance, cell_id: int) -> MapCell:
        if cell_id < 0 or cell_id >= len(current.cells):
            raise ValueError(self.content.text("errors.map.invalid_cell"))
        return current.cells[cell_id]

    @staticmethod
    def _adjacent(current: MapInstance, source: int, target: int) -> bool:
        source_x, source_y = source % current.width, source // current.width
        target_x, target_y = target % current.width, target // current.width
        return abs(source_x - target_x) + abs(source_y - target_y) == 1

    def _spend(self, profile: PlayerProfile, cost: int) -> None:
        if profile.stamina < cost:
            raise ValueError(self.content.text("errors.map.stamina"))
        profile.stamina -= cost

    def _gather_reason(self, cell: MapCell, available_now: bool, stamina: int, cost: int) -> str:
        if not cell.gatherable:
            return self.content.text("errors.map.not_gatherable")
        if cell.gathered:
            return self.content.text("errors.map.depleted")
        if not available_now:
            return self.content.text("errors.map.no_resources_now")
        if stamina < cost:
            return self.content.text("errors.map.stamina")
        return ""

    def _gather_rule_matches(
        self,
        rule: dict[str, Any],
        current: MapInstance,
        now: WorldTimeSnapshot,
    ) -> bool:
        conditions = rule.get("conditions") or {}
        if not self.clock.matches(conditions, now):
            return False
        regions = conditions.get("regions", [])
        return not regions or current.region_id in regions

    def _validate_configuration(self) -> None:
        if self.default_template_id not in self.templates:
            raise ValueError("Default map template does not exist")
        expected_positions = {
            (x, y)
            for y in range(int(self.world_grid["height"]))
            for x in range(int(self.world_grid["width"]))
        }
        if set(self.region_by_position) != expected_positions:
            raise ValueError("World grid must define exactly one region for every position")
        for region_id, region in self.regions.items():
            if region["region_id"] != region_id or region["world_id"] not in self.worlds:
                raise ValueError(f"Invalid region definition: {region_id}")
            if region_id not in self.template_by_region:
                raise ValueError(f"Region {region_id} has no map template")
        for template in self.templates.values():
            if template.world_id not in self.worlds or template.region_id not in self.regions:
                raise ValueError(f"Map template {template.template_id} references an unknown location")
            if self.regions[template.region_id]["world_id"] != template.world_id:
                raise ValueError(f"Map template {template.template_id} crosses world boundaries")
            unknown = (
                set(template.terrain_weights)
                | set(template.terrain_counts)
                | set(template.landmark_terrains.values())
            ) - set(self.terrains)
            if unknown:
                raise ValueError(f"Map template {template.template_id} has unknown terrains: {sorted(unknown)}")
            if template.terrain_counts and template.width and template.height:
                expected = template.width * template.height
                if sum(template.terrain_counts.values()) != expected:
                    raise ValueError(f"Map template {template.template_id} terrain counts must total {expected}")
                if any(cell_id < 0 or cell_id >= expected for cell_id in template.landmarks):
                    raise ValueError(f"Map template {template.template_id} has an invalid landmark cell")
        for terrain_id, terrain in self.terrains.items():
            for rule in terrain.get("gather_rules", []):
                if rule["resource_id"] not in self.catalog.resources:
                    raise ValueError(f"Terrain {terrain_id} references unknown resource {rule['resource_id']}")
                unknown_regions = set(rule.get("conditions", {}).get("regions", [])) - set(self.regions)
                if unknown_regions:
                    raise ValueError(
                        f"Terrain {terrain_id} gather rule references unknown regions: {sorted(unknown_regions)}"
                    )
        for race_id, race in self.catalog.races.items():
            birthplace = race["birthplace"]
            template = self.templates.get(birthplace["template_id"])
            if (
                template is None
                or template.region_id != birthplace["region_id"]
                or template.world_id != birthplace["world_id"]
                or int(birthplace["cell_id"]) not in template.landmarks
                or template.landmark_terrains.get(int(birthplace["cell_id"])) not in {"9", "10"}
            ):
                raise ValueError(f"Race {race_id} has an invalid configured birthplace")
        event_ids: set[str] = set()
        for rule in self.event_rules:
            event_id = rule.get("event_id")
            if not event_id or event_id in event_ids:
                raise ValueError(f"World event id must be present and unique: {event_id}")
            event_ids.add(event_id)
            unknown_terrains = set(rule.get("terrain_ids", [])) - set(self.terrains)
            if unknown_terrains:
                raise ValueError(
                    f"World event {event_id} references unknown terrains: {sorted(unknown_terrains)}"
                )
            unknown_regions = set(rule.get("region_ids", [])) - set(self.regions)
            unknown_regions.update(
                set(rule.get("conditions", {}).get("regions", [])) - set(self.regions)
            )
            if unknown_regions:
                raise ValueError(
                    f"World event {event_id} references unknown regions: {sorted(unknown_regions)}"
                )
            if rule.get("trigger_scope", "cell") not in {"cell", "region", "world"}:
                raise ValueError(f"World event {event_id} has an invalid trigger scope")
            if int(rule.get("cooldown_days", 1)) < 1:
                raise ValueError(f"World event {event_id} cooldown must be at least one day")
            max_triggers = rule.get("max_triggers")
            if max_triggers is not None and int(max_triggers) < 1:
                raise ValueError(f"World event {event_id} max triggers must be positive")
            if rule.get("persistence", "none") not in {
                "none",
                "while_conditions_match",
                "until_resolved",
            }:
                raise ValueError(f"World event {event_id} has an invalid persistence mode")
            action_ids: set[str] = set()
            for action in rule.get("actions", []):
                action_id = action.get("action_id")
                if not action_id or action_id in action_ids:
                    raise ValueError(f"World event {event_id} action ids must be present and unique")
                action_ids.add(action_id)
                if action.get("resolution", "keep_active") not in {"keep_active", "end_event"}:
                    raise ValueError(f"World event {event_id} action {action_id} has an invalid resolution")
                if action.get("kind", "narrative") not in {
                    "narrative", "open_npc", "start_quest", "npc_combat", "monster_combat", "use_item"
                }:
                    raise ValueError(f"World event {event_id} action {action_id} has an invalid kind")
                if action.get("kind") == "use_item":
                    requirements = action.get("item_requirements")
                    if not isinstance(requirements, dict) or not any(
                        requirements.get(key) for key in ("categories", "all_tags", "any_tags")
                    ):
                        raise ValueError(
                            f"World event {event_id} action {action_id} requires item matching rules"
                        )
