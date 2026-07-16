from __future__ import annotations

import asyncio
import random
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass(slots=True)
class GameRoom:
    room_id: str
    p1_id: str
    p2_id: str | None = None
    mode: str = "WAITING"
    is_started: bool = False
    thread_id: str = field(default_factory=lambda: f"room_{uuid.uuid4().hex[:12]}")
    p1_ws: Any = None
    p2_ws: Any = None
    p1_prep: dict[str, Any] | None = None
    p2_prep: dict[str, Any] | str | None = None
    p1_act: str | None = None
    p2_act: str | None = None
    npc_id: str | None = None
    npc_trigger_id: str | None = None
    npc_outcome_recorded: bool = False
    action_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state_lock: RLock = field(default_factory=RLock)

    def is_member(self, player_id: str) -> bool:
        return player_id == self.p1_id or player_id == self.p2_id

    async def attach(self, player_id: str, websocket: Any) -> bool:
        if player_id == self.p1_id:
            self.p1_ws = websocket
            return True
        if player_id == self.p2_id:
            self.p2_ws = websocket
            return False
        raise PermissionError(player_id)

    async def broadcast(self, data: dict[str, Any]) -> None:
        for websocket in (self.p1_ws, self.p2_ws):
            if websocket is None:
                continue
            try:
                await websocket.send_json(data)
            except Exception:
                continue


class InMemoryRoomRepository:
    def __init__(self):
        self._rooms: dict[str, GameRoom] = {}
        self._lock = RLock()

    def create(self, player_id: str) -> GameRoom:
        with self._lock:
            room_id = self._next_id()
            room = GameRoom(room_id=room_id, p1_id=player_id)
            self._rooms[room_id] = room
            return room

    def create_npc(self, player_id: str, npc_id: str, trigger_id: str) -> GameRoom:
        with self._lock:
            room_id = self._next_id()
            room = GameRoom(
                room_id=room_id,
                p1_id=player_id,
                p2_id=f"NPC_{npc_id}",
                mode="PvE",
                is_started=True,
                npc_id=npc_id,
                npc_trigger_id=trigger_id,
            )
            self._rooms[room_id] = room
            return room

    def get(self, room_id: str) -> GameRoom:
        with self._lock:
            try:
                return self._rooms[room_id]
            except KeyError as exc:
                raise KeyError(room_id) from exc

    def remove(self, room_id: str) -> GameRoom | None:
        with self._lock:
            return self._rooms.pop(room_id, None)

    def find_by_thread(self, thread_id: str) -> GameRoom | None:
        with self._lock:
            return next((room for room in self._rooms.values() if room.thread_id == thread_id), None)

    def _next_id(self) -> str:
        while True:
            room_id = str(random.SystemRandom().randint(1000, 9999))
            if room_id not in self._rooms:
                return room_id
