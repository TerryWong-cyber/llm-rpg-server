from __future__ import annotations

import random
import uuid
from typing import Any, Protocol

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import PlayerRepository
from llm_rpg_server.shared.config import ContentProvider

from .models import EncounterResult, MapCell, MapInstance, MapScale, MapTemplate


class EncounterResolver(Protocol):
    def resolve(
        self,
        player_id: str,
        map_instance: MapInstance,
        cell: MapCell,
        trigger: str,
    ) -> EncounterResult | None: ...


class ExplorationService:
    def __init__(
        self,
        players: PlayerRepository,
        catalog: Catalog,
        content: ContentProvider,
        encounter_resolver: EncounterResolver | None = None,
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
        self.gather_quantity = payload["gather_quantity"]
        self.terrains = payload["terrains"]
        self.templates = {
            template_id: self._template(definition)
            for template_id, definition in payload["templates"].items()
        }
        self.default_template_id = payload["default_template_id"]
        self._validate_configuration()

    def set_encounter_resolver(self, resolver: EncounterResolver) -> None:
        self.encounter_resolver = resolver

    def list_templates(self) -> list[dict[str, Any]]:
        return [template.model_dump(mode="json") for template in self.templates.values()]

    def enter(
        self,
        player_id: str,
        template_id: str | None = None,
        *,
        refresh: bool = False,
        seed: int | None = None,
    ) -> tuple[MapInstance, EncounterResult | None]:
        chosen_template = template_id or self.default_template_id
        if chosen_template not in self.templates:
            raise ValueError(self.content.text("errors.map.invalid_template"))
        with self.players.transaction(player_id) as profile:
            current = MapInstance.model_validate(profile.current_map) if profile.current_map else None
            if current is None or refresh or current.template_id != chosen_template:
                current = self._generate(self.templates[chosen_template], seed)
                current.cells[0].explored = True
                profile.current_map = current.model_dump(mode="json")
            encounter = self._resolve(player_id, current, current.cells[current.current_cell_id], "on_enter_map")
            profile.current_map = current.model_dump(mode="json")
            return current, encounter

    def move(self, player_id: str, cell_id: int) -> tuple[MapInstance, EncounterResult | None]:
        with self.players.transaction(player_id) as profile:
            if not profile.current_map:
                raise RuntimeError(self.content.text("errors.map.not_entered"))
            current = MapInstance.model_validate(profile.current_map)
            cell = self._cell(current, cell_id)
            if not cell.passable:
                raise ValueError(self.content.text("errors.map.impassable"))
            if not self._adjacent(current, current.current_cell_id, cell_id):
                raise ValueError(self.content.text("errors.map.invalid_cell"))
            current.current_cell_id = cell_id
            cell.explored = True
            encounter = self._resolve(player_id, current, cell, "on_enter_cell")
            profile.current_map = current.model_dump(mode="json")
            return current, encounter

    def gather(self, player_id: str, cell_id: int) -> tuple[dict[str, int], MapInstance, EncounterResult | None]:
        with self.players.transaction(player_id) as profile:
            if not profile.current_map:
                raise RuntimeError(self.content.text("errors.map.not_entered"))
            current = MapInstance.model_validate(profile.current_map)
            cell = self._cell(current, cell_id)
            if cell.gathered:
                raise ValueError(self.content.text("errors.map.depleted"))
            terrain = self.terrains[cell.terrain_id]
            rng = random.Random(f"{current.seed}:gather:{cell_id}")
            loot = {
                resource_id: rng.randint(
                    int(self.gather_quantity["minimum"]),
                    int(self.gather_quantity["maximum"]),
                )
                for resource_id, probability in terrain.get("drops", {}).items()
                if rng.random() <= probability
            }
            cell.gathered = True
            cell.explored = True
            for resource_id, quantity in loot.items():
                profile.inventory.materials[resource_id] = profile.inventory.materials.get(resource_id, 0) + quantity
            encounter = self._resolve(player_id, current, cell, "on_gather")
            profile.current_map = current.model_dump(mode="json")
            return loot, current, encounter

    def _template(self, definition: dict[str, Any]) -> MapTemplate:
        payload = dict(definition)
        scale = MapScale(payload["scale"])
        if scale is not MapScale.CUSTOM:
            dimensions = self.scale_defaults[scale.value]
            payload.setdefault("width", dimensions["width"])
            payload.setdefault("height", dimensions["height"])
        return MapTemplate.model_validate(payload)

    def _generate(self, template: MapTemplate, seed: int | None) -> MapInstance:
        actual_seed = seed if seed is not None else random.SystemRandom().randint(0, 2**31 - 1)
        rng = random.Random(actual_seed)
        terrain_ids = list(template.terrain_weights)
        weights = list(template.terrain_weights.values())
        cells: list[MapCell] = []
        assert template.width is not None and template.height is not None
        for cell_id in range(template.width * template.height):
            terrain_id = rng.choices(terrain_ids, weights=weights, k=1)[0]
            terrain = self.terrains[terrain_id]
            cells.append(MapCell(
                cell_id=cell_id,
                x=cell_id % template.width,
                y=cell_id // template.width,
                terrain_id=terrain_id,
                landmark_id=template.landmarks.get(cell_id),
                passable=terrain.get("passable", True),
            ))
        cells[0].passable = True
        return MapInstance(
            map_id=f"map_{uuid.uuid4().hex[:10]}",
            template_id=template.template_id,
            world_id=template.world_id,
            region_id=template.region_id,
            scale=template.scale,
            width=template.width,
            height=template.height,
            seed=actual_seed,
            config_version=self.schema_version,
            cells=cells,
        )

    def _resolve(self, player_id: str, current: MapInstance, cell: MapCell, trigger: str) -> EncounterResult | None:
        if self.encounter_resolver is None:
            return None
        return self.encounter_resolver.resolve(player_id, current, cell, trigger)

    def _cell(self, current: MapInstance, cell_id: int) -> MapCell:
        if cell_id < 0 or cell_id >= len(current.cells):
            raise ValueError(self.content.text("errors.map.invalid_cell"))
        return current.cells[cell_id]

    @staticmethod
    def _adjacent(current: MapInstance, source: int, target: int) -> bool:
        source_x, source_y = source % current.width, source // current.width
        target_x, target_y = target % current.width, target // current.width
        return abs(source_x - target_x) + abs(source_y - target_y) == 1

    def _validate_configuration(self) -> None:
        if self.default_template_id not in self.templates:
            raise ValueError("Default map template does not exist")
        for region_id, region in self.regions.items():
            if region["region_id"] != region_id or region["world_id"] not in self.worlds:
                raise ValueError(f"Invalid region definition: {region_id}")
        for template in self.templates.values():
            if template.world_id not in self.worlds or template.region_id not in self.regions:
                raise ValueError(f"Map template {template.template_id} references an unknown location")
            if self.regions[template.region_id]["world_id"] != template.world_id:
                raise ValueError(f"Map template {template.template_id} crosses world boundaries")
