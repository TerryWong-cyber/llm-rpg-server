from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from threading import RLock
from typing import Protocol

from .models import MemoryEntry, NPCProfile, NPCRelationship


class WorldRepository(Protocol):
    def register_npc(self, npc: NPCProfile, overwrite: bool = False) -> NPCProfile: ...

    def get_npc(self, npc_id: str) -> NPCProfile: ...

    def list_npcs(self, terrain_id: str | None = None, cell_id: int | None = None) -> list[NPCProfile]: ...

    def get_or_create_relationship(self, npc: NPCProfile, player_id: str) -> NPCRelationship: ...

    def save_relationship(self, relationship: NPCRelationship) -> NPCRelationship: ...

    def append_shared_memory(
        self,
        *,
        npc_id: str,
        player_id: str,
        npc_summary: str,
        player_summary: str,
        tags: list[str],
        importance: int = 2,
        facts: dict | None = None,
    ) -> None: ...

    def append_player_memory(
        self,
        player_id: str,
        summary: str,
        tags: list[str],
        importance: int = 2,
        facts: dict | None = None,
    ) -> None: ...

    def list_npc_memories(self, npc_id: str, player_id: str, limit: int = 8) -> list[MemoryEntry]: ...

    def list_player_memories(self, player_id: str, limit: int = 50) -> list[MemoryEntry]: ...

    def npc_has_memory_tag(self, npc_id: str, player_id: str, tag: str) -> bool: ...

    def record_world_fact(self, summary: str, tags: list[str], facts: dict | None = None) -> None: ...

    def list_world_facts(self, limit: int = 50) -> list[MemoryEntry]: ...


class InMemoryWorldRepository:
    def __init__(self):
        self._npcs: dict[str, NPCProfile] = {}
        self._relationships: dict[tuple[str, str], NPCRelationship] = {}
        self._npc_memories: dict[tuple[str, str], list[MemoryEntry]] = defaultdict(list)
        self._player_memories: dict[str, list[MemoryEntry]] = defaultdict(list)
        self._world_facts: list[MemoryEntry] = []
        self._lock = RLock()

    def register_npc(self, npc: NPCProfile, overwrite: bool = False) -> NPCProfile:
        with self._lock:
            if npc.npc_id in self._npcs and not overwrite:
                return deepcopy(self._npcs[npc.npc_id])
            self._npcs[npc.npc_id] = deepcopy(npc)
            return deepcopy(npc)

    def get_npc(self, npc_id: str) -> NPCProfile:
        with self._lock:
            try:
                return deepcopy(self._npcs[npc_id])
            except KeyError as exc:
                raise KeyError(npc_id) from exc

    def list_npcs(self, terrain_id: str | None = None, cell_id: int | None = None) -> list[NPCProfile]:
        with self._lock:
            values = list(self._npcs.values())
            if terrain_id is not None:
                values = [npc for npc in values if npc.location.terrain_id == terrain_id]
            if cell_id is not None:
                values = [npc for npc in values if not npc.location.cell_ids or cell_id in npc.location.cell_ids]
            return deepcopy(values)

    def get_or_create_relationship(self, npc: NPCProfile, player_id: str) -> NPCRelationship:
        key = (npc.npc_id, player_id)
        with self._lock:
            if key not in self._relationships:
                initial = npc.initial_disposition
                self._relationships[key] = NPCRelationship(
                    npc_id=npc.npc_id,
                    player_id=player_id,
                    affinity=initial.affinity,
                    trust=initial.trust,
                    respect=initial.respect,
                    hostility=initial.hostility,
                )
            return deepcopy(self._relationships[key])

    def save_relationship(self, relationship: NPCRelationship) -> NPCRelationship:
        with self._lock:
            self._relationships[(relationship.npc_id, relationship.player_id)] = deepcopy(relationship)
            return deepcopy(relationship)

    def append_shared_memory(
        self,
        *,
        npc_id: str,
        player_id: str,
        npc_summary: str,
        player_summary: str,
        tags: list[str],
        importance: int = 2,
        facts: dict | None = None,
    ) -> None:
        shared_facts = facts or {}
        with self._lock:
            self._npc_memories[(npc_id, player_id)].append(MemoryEntry(
                owner_type="npc",
                owner_id=npc_id,
                counterpart_id=player_id,
                summary=npc_summary,
                tags=list(tags),
                importance=importance,
                facts=shared_facts,
            ))
            self._player_memories[player_id].append(MemoryEntry(
                owner_type="player",
                owner_id=player_id,
                counterpart_id=npc_id,
                summary=player_summary,
                tags=list(tags),
                importance=importance,
                facts=shared_facts,
            ))

    def append_player_memory(
        self,
        player_id: str,
        summary: str,
        tags: list[str],
        importance: int = 2,
        facts: dict | None = None,
    ) -> None:
        with self._lock:
            self._player_memories[player_id].append(MemoryEntry(
                owner_type="player",
                owner_id=player_id,
                summary=summary,
                tags=list(tags),
                importance=importance,
                facts=facts or {},
            ))

    def list_npc_memories(self, npc_id: str, player_id: str, limit: int = 8) -> list[MemoryEntry]:
        with self._lock:
            values = deepcopy(self._npc_memories[(npc_id, player_id)])
        values.sort(key=lambda item: (item.importance, item.created_at), reverse=True)
        return values[:limit]

    def list_player_memories(self, player_id: str, limit: int = 50) -> list[MemoryEntry]:
        with self._lock:
            values = deepcopy(self._player_memories[player_id])
        values.sort(key=lambda item: item.created_at, reverse=True)
        return values[:limit]

    def npc_has_memory_tag(self, npc_id: str, player_id: str, tag: str) -> bool:
        with self._lock:
            return any(tag in memory.tags for memory in self._npc_memories[(npc_id, player_id)])

    def record_world_fact(self, summary: str, tags: list[str], facts: dict | None = None) -> None:
        with self._lock:
            self._world_facts.append(MemoryEntry(
                owner_type="world",
                owner_id="world",
                summary=summary,
                tags=list(tags),
                importance=4,
                facts=facts or {},
            ))

    def list_world_facts(self, limit: int = 50) -> list[MemoryEntry]:
        with self._lock:
            values = deepcopy(self._world_facts)
        values.sort(key=lambda item: item.created_at, reverse=True)
        return values[:limit]
