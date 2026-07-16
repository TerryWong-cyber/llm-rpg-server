from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock


class InMemoryEncounterHistory:
    def __init__(self):
        self._triggered: dict[tuple[str, str], list[datetime]] = {}
        self._attempts: dict[tuple[str, str], int] = {}
        self._lock = RLock()

    def next_attempt(self, player_id: str, encounter_id: str) -> int:
        key = (player_id, encounter_id)
        with self._lock:
            attempt = self._attempts.get(key, 0)
            self._attempts[key] = attempt + 1
            return attempt

    def can_trigger(self, player_id: str, encounter_id: str, repeatable: bool, cooldown_seconds: int) -> bool:
        key = (player_id, encounter_id)
        now = datetime.now(timezone.utc)
        with self._lock:
            records = self._triggered.get(key, [])
            if records and not repeatable:
                return False
            if records and cooldown_seconds > 0 and (now - records[-1]).total_seconds() < cooldown_seconds:
                return False
            return True

    def record(self, player_id: str, encounter_id: str) -> None:
        key = (player_id, encounter_id)
        with self._lock:
            self._triggered.setdefault(key, []).append(datetime.now(timezone.utc))

