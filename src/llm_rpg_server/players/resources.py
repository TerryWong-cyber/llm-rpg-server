from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .models import PlayerProfile, SleepState
from .repository import PlayerRepository


class GameClock(Protocol):
    def snapshot(self): ...


class ResourceLifecycleService:
    """Settles passive stamina recovery and authoritative, interruptible sleep."""

    def __init__(self, players: PlayerRepository, clock: GameClock):
        self.players = players
        self.clock = clock

    def settle(self, player_id: str, *, interrupt_sleep: bool = False) -> PlayerProfile:
        now = self.clock.snapshot()
        observed_at = datetime.fromisoformat(now.observed_at)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        with self.players.transaction(player_id) as profile:
            was_sleeping = profile.sleep is not None
            if profile.sleep is not None:
                complete = self._settle_sleep(profile, observed_at)
                if interrupt_sleep and not complete:
                    profile.sleep = None
                if profile.sleep is None:
                    profile.last_stamina_recovery_game_hour = now.total_game_hours
            if profile.sleep is None and not was_sleeping:
                self._settle_passive_stamina(profile, now.total_game_hours)
        return self.players.get(player_id)

    def start_sleep(self, player_id: str, location_kind: str) -> PlayerProfile:
        self.settle(player_id, interrupt_sleep=True)
        now = self.clock.snapshot()
        started_at = datetime.fromisoformat(now.observed_at)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        with self.players.transaction(player_id) as profile:
            profile.sleep = SleepState(
                started_at=started_at,
                started_game_hour=now.total_game_hours,
                start_hp=profile.current_hp,
                start_mp=profile.current_mp,
                start_stamina=profile.stamina,
                location_kind=location_kind,
            )
            profile.last_stamina_recovery_game_hour = now.total_game_hours
        return self.players.get(player_id)

    def is_sleeping(self, player_id: str) -> bool:
        return self.settle(player_id).sleep is not None

    @staticmethod
    def _settle_sleep(profile: PlayerProfile, observed_at: datetime) -> bool:
        sleep = profile.sleep
        if sleep is None:
            return True
        elapsed_seconds = max(0, int((observed_at - sleep.started_at).total_seconds()))
        elapsed_hours = min(
            sleep.duration_game_hours,
            elapsed_seconds * sleep.duration_game_hours // sleep.duration_seconds,
        )
        if elapsed_hours >= sleep.duration_game_hours:
            profile.current_hp = profile.max_hp
            profile.current_mp = profile.max_mp
            profile.stamina = profile.max_stamina
            profile.combat_statuses = []
            profile.sleep = None
            return True
        profile.current_hp = min(
            profile.max_hp,
            sleep.start_hp + (profile.max_hp - sleep.start_hp) * elapsed_hours // sleep.duration_game_hours,
        )
        profile.current_mp = min(
            profile.max_mp,
            sleep.start_mp + (profile.max_mp - sleep.start_mp) * elapsed_hours // sleep.duration_game_hours,
        )
        profile.stamina = min(
            profile.max_stamina,
            sleep.start_stamina
            + (profile.max_stamina - sleep.start_stamina) * elapsed_hours // sleep.duration_game_hours,
        )
        return False

    @staticmethod
    def _settle_passive_stamina(profile: PlayerProfile, total_game_hours: int) -> None:
        previous = profile.last_stamina_recovery_game_hour
        if previous is None:
            profile.last_stamina_recovery_game_hour = total_game_hours
            return
        elapsed = max(0, total_game_hours - previous)
        if elapsed:
            profile.stamina = min(profile.max_stamina, profile.stamina + elapsed)
            profile.last_stamina_recovery_game_hour = total_game_hours
