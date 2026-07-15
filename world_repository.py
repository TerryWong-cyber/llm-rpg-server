"""Persistence boundary for NPCs, relationships, and memories.

The current game has an in-memory player database, so this implementation is
also in-memory.  Callers depend on this repository API rather than its dicts;
replacing it with SQLite/Redis/MySQL later only requires a new adapter.
"""

from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Dict, Iterable, List, Optional, Tuple

from npc_models import MemoryEntry, NPCProfile, NPCRelationship


def _copy(model):
    """Works with both Pydantic v1 and v2 without leaking mutable state."""
    if hasattr(model, "model_copy"):
        return model.model_copy(deep=True)
    return model.copy(deep=True)


class InMemoryWorldRepository:
    def __init__(self):
        self._lock = RLock()
        self._npcs: Dict[str, NPCProfile] = {}
        self._relationships: Dict[Tuple[str, str], NPCRelationship] = {}
        self._npc_memories: Dict[Tuple[str, str], List[MemoryEntry]] = defaultdict(list)
        self._player_memories: Dict[str, List[MemoryEntry]] = defaultdict(list)
        self._world_facts: List[MemoryEntry] = []

    def register_npc(self, npc: NPCProfile, overwrite: bool = False) -> NPCProfile:
        with self._lock:
            if npc.npc_id in self._npcs and not overwrite:
                return _copy(self._npcs[npc.npc_id])
            self._npcs[npc.npc_id] = _copy(npc)
            return _copy(npc)

    def get_npc(self, npc_id: str) -> NPCProfile:
        with self._lock:
            if npc_id not in self._npcs:
                raise KeyError(npc_id)
            return _copy(self._npcs[npc_id])

    def list_npcs(self, terrain_id: Optional[str] = None, cell_id: Optional[int] = None) -> List[NPCProfile]:
        with self._lock:
            npcs: Iterable[NPCProfile] = self._npcs.values()
            if terrain_id is not None:
                npcs = (npc for npc in npcs if npc.location.terrain_id == terrain_id)
            if cell_id is not None:
                npcs = (npc for npc in npcs if not npc.location.cell_ids or cell_id in npc.location.cell_ids)
            return [_copy(npc) for npc in npcs]

    def get_or_create_relationship(self, npc: NPCProfile, player_id: str) -> NPCRelationship:
        key = (npc.npc_id, player_id)
        with self._lock:
            if key not in self._relationships:
                disposition = npc.initial_disposition
                self._relationships[key] = NPCRelationship(
                    npc_id=npc.npc_id,
                    player_id=player_id,
                    affinity=disposition.affinity,
                    trust=disposition.trust,
                    respect=disposition.respect,
                    hostility=disposition.hostility,
                )
            return _copy(self._relationships[key])

    def save_relationship(self, relationship: NPCRelationship) -> NPCRelationship:
        with self._lock:
            key = (relationship.npc_id, relationship.player_id)
            self._relationships[key] = _copy(relationship)
            return _copy(relationship)

    def append_shared_memory(
        self,
        *,
        npc_id: str,
        player_id: str,
        npc_summary: str,
        player_summary: str,
        tags: List[str],
        importance: int = 2,
        facts: Optional[Dict] = None,
    ) -> None:
        facts = facts or {}
        with self._lock:
            self._npc_memories[(npc_id, player_id)].append(MemoryEntry(
                owner_type="npc",
                owner_id=npc_id,
                counterpart_id=player_id,
                summary=npc_summary,
                tags=list(tags),
                importance=importance,
                facts=facts,
            ))
            self._player_memories[player_id].append(MemoryEntry(
                owner_type="player",
                owner_id=player_id,
                counterpart_id=npc_id,
                summary=player_summary,
                tags=list(tags),
                importance=importance,
                facts=facts,
            ))

    def append_player_memory(
        self,
        player_id: str,
        summary: str,
        tags: List[str],
        importance: int = 2,
        facts: Optional[Dict] = None,
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

    def list_npc_memories(self, npc_id: str, player_id: str, limit: int = 8) -> List[MemoryEntry]:
        with self._lock:
            memories = list(self._npc_memories[(npc_id, player_id)])
        memories.sort(key=lambda item: (item.importance, item.created_at), reverse=True)
        return [_copy(item) for item in memories[:limit]]

    def list_player_memories(self, player_id: str, limit: int = 50) -> List[MemoryEntry]:
        with self._lock:
            memories = list(self._player_memories[player_id])
        memories.sort(key=lambda item: item.created_at, reverse=True)
        return [_copy(item) for item in memories[:limit]]

    def npc_has_memory_tag(self, npc_id: str, player_id: str, tag: str) -> bool:
        with self._lock:
            return any(tag in memory.tags for memory in self._npc_memories[(npc_id, player_id)])

    def record_world_fact(self, summary: str, tags: List[str], facts: Optional[Dict] = None) -> None:
        with self._lock:
            self._world_facts.append(MemoryEntry(
                owner_type="world",
                owner_id="world",
                summary=summary,
                tags=list(tags),
                importance=4,
                facts=facts or {},
            ))

    def list_world_facts(self, limit: int = 50) -> List[MemoryEntry]:
        with self._lock:
            facts = list(self._world_facts)
        facts.sort(key=lambda item: item.created_at, reverse=True)
        return [_copy(item) for item in facts[:limit]]
