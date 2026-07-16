from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from llm_rpg_server.api.dependencies import container_from_request
from llm_rpg_server.api.schemas import CellRequest, EnterMapRequest
from llm_rpg_server.bootstrap import AppContainer
from llm_rpg_server.exploration import MapInstance

router = APIRouter(prefix="/api/map", tags=["exploration"])
Container = Annotated[AppContainer, Depends(container_from_request)]


@router.get("/templates")
def map_templates(container: Container):
    return {
        "templates": container.exploration.list_templates(),
        "worlds": container.exploration.worlds,
        "regions": container.exploration.regions,
    }


@router.post("/enter")
def enter_map(request: EnterMapRequest, container: Container):
    current, encounter = container.exploration.enter(
        request.player_id,
        request.template_id,
        refresh=request.refresh,
        seed=request.seed,
    )
    return _map_response(container, request.player_id, current, encounter)


@router.post("/move")
def move(request: CellRequest, container: Container):
    current, encounter = container.exploration.move(request.player_id, request.cell_id)
    return _map_response(container, request.player_id, current, encounter)


@router.post("/gather")
def gather(request: CellRequest, container: Container):
    loot, current, encounter = container.exploration.gather(request.player_id, request.cell_id)
    loot_labels = [
        container.content.text(
            "exploration.loot_item",
            emoji=container.catalog.resources[item_id]["emoji"],
            name=container.catalog.resources[item_id]["name"],
            quantity=quantity,
        )
        for item_id, quantity in loot.items()
    ]
    separator = container.content.text("exploration.loot_separator")
    message = separator.join(loot_labels) if loot_labels else container.content.text("exploration.nothing_found")
    cell = current.cells[request.cell_id]
    terrain_name = container.exploration.terrains.get(cell.terrain_id, {}).get(
        "name", container.content.text("exploration.unknown_terrain")
    )
    container.npc_interactions.record_player_event(
        request.player_id,
        container.content.text("exploration.player_memory", terrain_name=terrain_name, loot_message=message),
        tags=["exploration", "gather", f"terrain:{cell.terrain_id}"],
        importance=2 if loot else 1,
    )
    profile = container.players.get(request.player_id)
    return {
        "status": "success",
        "msg": message,
        "loot": loot,
        "cell_id": request.cell_id,
        "inventory_materials": profile.inventory.materials,
        "map": current.model_dump(mode="json"),
        "encounter": encounter.model_dump(mode="json") if encounter else None,
    }


def _map_response(container: AppContainer, player_id: str, current: MapInstance, encounter):
    profile = container.players.get(player_id)
    cells = []
    for cell in current.cells:
        payload = cell.model_dump(mode="json")
        payload["is_gathered"] = payload["gathered"]
        cells.append(payload)
    return {
        "map": current.model_dump(mode="json"),
        "map_grid": cells,
        "terrains_meta": container.exploration.terrains,
        "resources_meta": container.catalog.resources,
        "inventory_materials": profile.inventory.materials,
        "encounter": encounter.model_dump(mode="json") if encounter else None,
    }
