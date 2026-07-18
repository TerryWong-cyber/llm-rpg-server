from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from llm_rpg_server.api.dependencies import container_from_request
from llm_rpg_server.api.schemas import AddAIRequest, PlayerRequest, RoomJoinRequest
from llm_rpg_server.bootstrap import AppContainer

router = APIRouter(prefix="/api/room", tags=["rooms"])
Container = Annotated[AppContainer, Depends(container_from_request)]


@router.post("/create")
def create_room(request: PlayerRequest, container: Container):
    room = container.combat.create_room(request.player_id)
    return {"room_id": room.room_id}


@router.post("/join")
def join_room(request: RoomJoinRequest, container: Container):
    room = container.combat.join_room(request.room_id, request.player_id)
    return {"room_id": room.room_id, "mode": room.mode}


@router.post("/add_ai")
async def add_ai(request: AddAIRequest, container: Container):
    room = await asyncio.to_thread(container.combat.add_ai, request.room_id)
    await room.broadcast({"event": "game_start", "snapshot": container.combat.snapshot(room)})
    return {"room_id": room.room_id, "mode": room.mode}
