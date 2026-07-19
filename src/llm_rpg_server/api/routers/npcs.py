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
def list_npcs(
    container: Container,
    terrain_id: str | None = None,
    cell_id: int | None = None,
    player_id: str | None = None,
):
    return {"npcs": container.npc_interactions.list_npcs(terrain_id, cell_id, player_id)}


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
    container.resources.settle(request.player_id, interrupt_sleep=True)
    response = container.npc_interactions.interact(npc_id, request.player_id, request.message)
    response["profile"] = container.players.get(request.player_id).model_dump(mode="json")
    return response


@router.get("/npcs/{npc_id}/memories")
def npc_memories(npc_id: str, player_id: str, container: Container):
    container.players.get(player_id)
    return {"npc_id": npc_id, "memories": container.npc_interactions.npc_memories(npc_id, player_id)}


@router.get("/npcs/{npc_id}/conversations")
def npc_conversations(npc_id: str, player_id: str, container: Container):
    container.players.get(player_id)
    return {
        "npc_id": npc_id,
        "conversations": container.npc_interactions.conversations(npc_id, player_id),
    }


@router.get("/players/{player_id}/memories")
def player_memories(player_id: str, container: Container):
    container.players.get(player_id)
    return {"player_id": player_id, "memories": container.npc_interactions.player_memories(player_id)}


@router.get("/players/{player_id}/journal")
def player_journal(player_id: str, container: Container):
    profile = container.resources.settle(player_id)
    contacts = []
    for npc_id in profile.encountered_npc_ids:
        npc = container.world_repository.get_npc(npc_id)
        relationship = container.world_repository.get_or_create_relationship(npc, player_id)
        contacts.append({
            "npc": container.npc_interactions.public_npc(npc),
            "relationship": relationship,
            "memories": container.npc_interactions.npc_memories(npc_id, player_id),
            "conversations": container.npc_interactions.conversations(npc_id, player_id),
        })

    def quest_payload(quest):
        payload = quest.model_dump(mode="json")
        objectives = []
        for requirement in quest.requirements:
            item = dict(requirement)
            if item.get("kind") == "region":
                current = (profile.current_map or {}).get("region_id")
                item.update({"current": current, "completed": current == item.get("region_id")})
            else:
                collection = (
                    profile.inventory.materials
                    if item.get("item_type") == "material" else profile.inventory.items
                )
                current = collection.get(str(item.get("item_id")), 0)
                item.update({"current": current, "completed": current >= int(item.get("quantity", 1))})
            objectives.append(item)
        payload["requirements"] = objectives
        payload["related_npcs"] = [
            container.npc_interactions.public_npc(container.world_repository.get_npc(npc_id))
            for npc_id in quest.related_npc_ids
        ]
        return payload

    return {
        "events": [entry.model_dump(mode="json") for entry in reversed(profile.world_event_log)],
        "active_quests": [quest_payload(item) for item in profile.active_quests.values()],
        "completed_quests": [quest_payload(item) for item in profile.quest_history.values()],
        "contacts": contacts,
    }


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
    room = container.combat.start_npc_combat(request.player_id, npc_id, request.trigger_id)
    return {
        "status": "success",
        "room_id": room.room_id,
        "websocket_path": f"/ws/room/{room.room_id}/{request.player_id}",
        "snapshot": container.combat.snapshot(room),
    }
