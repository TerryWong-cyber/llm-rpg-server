from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from llm_rpg_server.api.dependencies import container_from_websocket

router = APIRouter()


@router.websocket("/ws/room/{room_id}/{player_id}")
async def room_socket(websocket: WebSocket, room_id: str, player_id: str):
    container = container_from_websocket(websocket)
    await websocket.accept()
    try:
        room = container.rooms.get(room_id)
        is_p1 = await room.attach(player_id, websocket)
    except (KeyError, PermissionError):
        await websocket.send_json({"event": "error", "msg": container.content.text("errors.room.invalid_member")})
        await websocket.close(code=1008)
        return
    async with room.action_lock:
        if await asyncio.to_thread(container.combat.start_pvp_if_ready, room):
            await room.broadcast({"event": "game_start", "snapshot": container.combat.snapshot(room)})
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            try:
                async with room.action_lock:
                    if action == "prep":
                        payload = {
                            "weapon_id": data.get("weapon_id"),
                            "armor_id": data.get("armor_id"),
                            "item_id": data.get("item_id"),
                        }
                        await room.broadcast({"event": "player_ready", "player_id": player_id, "phase": "prep"})
                        snapshot = await asyncio.to_thread(container.combat.submit_prep, room, player_id, payload)
                    elif action == "combat":
                        await room.broadcast({"event": "player_ready", "player_id": player_id, "phase": "combat"})
                        snapshot = await asyncio.to_thread(
                            container.combat.submit_action,
                            room,
                            player_id,
                            str(data.get("action_key", "")),
                            str(data["item_id"]) if data.get("item_id") else None,
                        )
                    else:
                        raise ValueError(container.content.text("errors.room.invalid_action"))
                    if snapshot:
                        await room.broadcast({"event": "snapshot", "snapshot": snapshot})
            except (ValueError, PermissionError, KeyError) as exc:
                room.p1_prep = None
                room.p2_prep = None
                room.p1_act = None
                room.p2_act = None
                await websocket.send_json({"event": "error", "msg": str(exc)})
    except WebSocketDisconnect:
        container.rooms.remove(room_id)
        await room.broadcast({"event": "error", "msg": container.content.text("errors.room.disconnected")})
