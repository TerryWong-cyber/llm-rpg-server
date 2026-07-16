from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from typing import Callable, ContextManager, Iterator, Protocol, TypeVar

from .models import PlayerProfile

T = TypeVar("T")


class PlayerRepository(Protocol):
    def exists(self, player_id: str) -> bool: ...

    def get(self, player_id: str) -> PlayerProfile: ...

    def create(self, profile: PlayerProfile) -> PlayerProfile: ...

    def transaction(self, player_id: str) -> ContextManager[PlayerProfile]: ...

    def update_once(
        self,
        player_id: str,
        operation_id: str,
        update: Callable[[PlayerProfile], T],
    ) -> tuple[bool, T | None]: ...


class InMemoryPlayerRepository:
    def __init__(self):
        self._players: dict[str, PlayerProfile] = {}
        self._completed_operations: set[tuple[str, str]] = set()
        self._lock = RLock()

    def exists(self, player_id: str) -> bool:
        with self._lock:
            return player_id in self._players

    def get(self, player_id: str) -> PlayerProfile:
        with self._lock:
            try:
                return deepcopy(self._players[player_id])
            except KeyError as exc:
                raise KeyError(player_id) from exc

    def create(self, profile: PlayerProfile) -> PlayerProfile:
        with self._lock:
            if profile.player_id in self._players:
                raise ValueError(f"Player already exists: {profile.player_id}")
            self._players[profile.player_id] = deepcopy(profile)
            return deepcopy(profile)

    @contextmanager
    def transaction(self, player_id: str) -> Iterator[PlayerProfile]:
        with self._lock:
            if player_id not in self._players:
                raise KeyError(player_id)
            working_copy = deepcopy(self._players[player_id])
            yield working_copy
            self._players[player_id] = deepcopy(working_copy)

    def update_once(
        self,
        player_id: str,
        operation_id: str,
        update: Callable[[PlayerProfile], T],
    ) -> tuple[bool, T | None]:
        key = (player_id, operation_id)
        with self._lock:
            if key in self._completed_operations:
                return False, None
            if player_id not in self._players:
                raise KeyError(player_id)
            working_copy = deepcopy(self._players[player_id])
            result = update(working_copy)
            self._players[player_id] = working_copy
            self._completed_operations.add(key)
            return True, result
