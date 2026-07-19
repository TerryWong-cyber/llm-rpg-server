from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from llm_rpg_server.api.dependencies import container_from_request
from llm_rpg_server.api.schemas import (
    CellRequest,
    DirectionRequest,
    EatRequest,
    EnterMapRequest,
    EventActionRequest,
    PlayerRequest,
)
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
        "world_grid": container.exploration.world_overview(),
        "world_time": container.exploration.time_snapshot().model_dump(mode="json"),
    }


@router.get("/time")
def world_time(container: Container):
    snapshot = container.exploration.time_snapshot()
    return {
        "world_time": snapshot.model_dump(mode="json"),
        "label": container.exploration.clock.label(snapshot),
    }


@router.get("/resources")
def player_resources(player_id: str, container: Container):
    profile = container.exploration.settle_resources(player_id)
    return {
        "player": _player_payload(container, profile),
        "world_time": container.exploration.time_snapshot().model_dump(mode="json"),
        "actions": {
            key: value.model_dump(mode="json")
            for key, value in container.exploration.actions(player_id).items()
        },
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


@router.post("/move-direction")
def move_direction(request: DirectionRequest, container: Container):
    current, encounter, transition = container.exploration.move_direction(
        request.player_id, request.direction
    )
    return _map_response(container, request.player_id, current, encounter, transition=transition)


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
    response = _map_response(container, request.player_id, current, encounter)
    response.update({
        "status": "success",
        "msg": message,
        "loot": loot,
        "cell_id": request.cell_id,
    })
    return response


@router.post("/eat")
def eat(request: EatRequest, container: Container):
    profile = container.exploration.eat(request.player_id, request.item_id)
    current = MapInstance.model_validate(profile.current_map) if profile.current_map else None
    if current is None:
        return {"status": "success", "profile": profile.model_dump(mode="json")}
    response = _map_response(container, request.player_id, current, None)
    response["status"] = "success"
    response["profile"] = profile.model_dump(mode="json")
    return response


@router.post("/camp")
def camp(request: PlayerRequest, container: Container):
    profile = container.exploration.camp(request.player_id)
    current = MapInstance.model_validate(profile.current_map)
    response = _map_response(container, request.player_id, current, None)
    response["status"] = "success"
    response["profile"] = profile.model_dump(mode="json")
    return response


@router.post("/inn")
def rest_at_inn(request: PlayerRequest, container: Container):
    profile = container.exploration.rest_at_inn(request.player_id)
    current = MapInstance.model_validate(profile.current_map)
    response = _map_response(container, request.player_id, current, None)
    response["status"] = "success"
    response["profile"] = profile.model_dump(mode="json")
    return response


@router.post("/event-action")
def event_action(request: EventActionRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
    outcome = container.world_events.perform(
        request.player_id,
        request.event_id,
        request.action_id,
        request.item_id,
        request.skill_id,
    )
    response = _map_response(container, request.player_id, outcome.current, None)
    response["interaction"] = outcome.interaction
    if outcome.combat_room:
        room = outcome.combat_room
        response["combat"] = {
            "status": "success",
            "room_id": room.room_id,
            "websocket_path": f"/ws/room/{room.room_id}/{request.player_id}",
            "snapshot": container.combat.snapshot(room),
        }
    else:
        response["combat"] = None
    return response


@router.post("/wake")
def wake(request: PlayerRequest, container: Container):
    profile = container.exploration.interrupt_sleep(request.player_id)
    current = MapInstance.model_validate(profile.current_map)
    response = _map_response(container, request.player_id, current, None)
    response["status"] = "success"
    response["profile"] = profile.model_dump(mode="json")
    return response


def _map_response(
    container: AppContainer,
    player_id: str,
    current: MapInstance,
    encounter,
    *,
    transition=None,
):
    profile = container.exploration.settle_resources(player_id)
    cells = []
    for cell in current.cells:
        payload = cell.model_dump(mode="json")
        payload["is_gathered"] = payload["gathered"]
        payload["active_event_ids"] = container.exploration.active_event_ids(
            profile, current, cell
        )
        cells.append(payload)
    return {
        "map": current.model_dump(mode="json"),
        "map_grid": cells,
        "terrains_meta": container.exploration.terrains,
        "resources_meta": container.catalog.resources,
        "inventory_materials": profile.inventory.materials,
        "player": _player_payload(container, profile),
        "world": container.exploration.world_overview(),
        "world_time": container.exploration.time_snapshot().model_dump(mode="json"),
        "actions": {
            key: value.model_dump(mode="json")
            for key, value in container.exploration.actions(player_id).items()
        },
        "event": _event_payload(container, player_id),
        "event_log": [entry.model_dump(mode="json") for entry in profile.world_event_log],
        "transition": transition.model_dump(mode="json") if transition else None,
        "encounter": encounter.model_dump(mode="json") if encounter else None,
    }


def _player_payload(container: AppContainer, profile):
    return {
        "current_hp": profile.current_hp,
        "max_hp": profile.max_hp,
        "current_mp": profile.current_mp,
        "max_mp": profile.max_mp,
        "stamina": profile.stamina,
        "max_stamina": profile.max_stamina,
        "combat_statuses": [item.model_dump(mode="json") for item in profile.combat_statuses],
        "exploration_effects": [item.model_dump(mode="json") for item in profile.exploration_effects],
        "inventory_items": profile.inventory.items,
        "last_camped_game_day": profile.last_camped_game_day,
        "sleep": profile.sleep.model_dump(mode="json") if profile.sleep else None,
        "progression": container.growth.public_progress(profile),
        "active_quests": {
            key: value.model_dump(mode="json")
            for key, value in profile.active_quests.items()
        },
        "completed_quests": list(profile.completed_quests),
    }


def _event_payload(container: AppContainer, player_id: str):
    event = container.exploration.pop_latest_event(player_id)
    if event is None:
        return None
    payload = event.model_dump(mode="json")
    eligible = container.world_events.item_options(player_id, event.event_id)
    action_options = container.world_events.action_options(player_id, event.event_id)
    visible_actions = []
    for action in payload.get("actions", []):
        options = action_options.get(action["action_id"], {})
        if options.get("visible", True) is False:
            continue
        action["eligible_items"] = eligible.get(action["action_id"], [])
        action["eligible_skills"] = options.get("eligible_skills", [])
        visible_actions.append(action)
    payload["actions"] = visible_actions
    return payload
