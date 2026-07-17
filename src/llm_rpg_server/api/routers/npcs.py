from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from llm_rpg_server.api.dependencies import container_from_request
from llm_rpg_server.api.schemas import NPCCombatStartRequest, NPCDialogueRequest
from llm_rpg_server.bootstrap import AppContainer
from llm_rpg_server.npcs.loader import seed_npcs

router = APIRouter(prefix="/api/world", tags=["world"])
Container = Annotated[AppContainer, Depends(container_from_request)]


@router.post("/npcs/seed-demo")
def seed_demo(container: Container):
    npcs = seed_npcs(container.world_repository, container.content)
    return {"status": "success", "npcs": [container.npc_interactions.public_npc(npc) for npc in npcs]}


@router.get("/npcs")
def list_npcs(container: Container, terrain_id: str | None = None, cell_id: int | None = None):
    return {"npcs": container.npc_interactions.list_npcs(terrain_id, cell_id)}


@router.get("/npcs/{npc_id}")
def get_npc(npc_id: str, container: Container, player_id: str | None = None):
    if player_id:
        container.players.get(player_id)
        return container.npc_interactions.get_npc_view(npc_id, player_id)
    npc = container.world_repository.get_npc(npc_id)
    return {"npc": container.npc_interactions.public_npc(npc)}


@router.post("/npcs/{npc_id}/dialogue")
def dialogue(npc_id: str, request: NPCDialogueRequest, container: Container):
    container.players.get(request.player_id)
    return container.npc_interactions.interact(npc_id, request.player_id, request.message)


@router.get("/npcs/{npc_id}/memories")
def npc_memories(npc_id: str, player_id: str, container: Container):
    container.players.get(player_id)
    return {"npc_id": npc_id, "memories": container.npc_interactions.npc_memories(npc_id, player_id)}


@router.get("/players/{player_id}/memories")
def player_memories(player_id: str, container: Container):
    container.players.get(player_id)
    return {"player_id": player_id, "memories": container.npc_interactions.player_memories(player_id)}


@router.get("/facts")
def world_facts(container: Container):
    return {"facts": container.npc_interactions.world_facts()}


@router.get("/monsters")
def list_monsters(container: Container):
    return {"monsters": [item.public_view() for item in container.monsters.list_all()]}


@router.get("/monsters/{monster_id}")
def get_monster(monster_id: str, container: Container):
    return {"monster": container.monsters.public_view(monster_id)}


@router.post("/npcs/{npc_id}/combat/start")
def start_npc_combat(npc_id: str, request: NPCCombatStartRequest, container: Container):
    container.exploration.require_stamina(request.player_id, "combat")
    room = container.combat.start_npc_combat(request.player_id, npc_id, request.trigger_id)
    container.exploration.spend_stamina(request.player_id, "combat")
    return {
        "status": "success",
        "room_id": room.room_id,
        "websocket_path": f"/ws/room/{room.room_id}/{request.player_id}",
        "snapshot": container.combat.snapshot(room),
    }
