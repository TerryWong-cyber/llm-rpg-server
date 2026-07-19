from __future__ import annotations

import uuid
from typing import Any, Literal, Protocol

from llm_rpg_server.shared.config import ContentProvider

from .models import CharacterChronicleEntry, PlayerProfile
from .repository import PlayerRepository

ChronicleCategory = Literal["origin", "growth", "skill", "quest", "exploration", "combat"]


class ChronicleClock(Protocol):
    def snapshot(self) -> Any: ...


class CharacterChronicleService:
    """Records a compact, server-authoritative history of meaningful character changes."""

    def __init__(
        self,
        players: PlayerRepository,
        content: ContentProvider,
        clock: ChronicleClock,
    ):
        self.players = players
        self.content = content
        self.clock = clock

    def text(self, key: str, **values: object) -> str:
        return self.content.text(f"chronicle.{key}", **values)

    def record_player(
        self,
        player_id: str,
        category: ChronicleCategory,
        title: str,
        description: str,
        *,
        emoji: str = "✦",
        source_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> CharacterChronicleEntry:
        with self.players.transaction(player_id) as profile:
            return self.record(
                profile,
                category,
                title,
                description,
                emoji=emoji,
                source_id=source_id,
                details=details,
            )

    def record(
        self,
        profile: PlayerProfile,
        category: ChronicleCategory,
        title: str,
        description: str,
        *,
        emoji: str = "✦",
        source_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> CharacterChronicleEntry:
        now = self.clock.snapshot()
        entry = CharacterChronicleEntry(
            entry_id=f"chronicle_{uuid.uuid4().hex}",
            category=category,
            title=title,
            description=description,
            emoji=emoji,
            source_id=source_id,
            game_hour=now.total_game_hours,
            year=now.year,
            month=now.month,
            day=now.day,
            hour=now.hour,
            details=details or {},
        )
        profile.chronicle.append(entry)
        if len(profile.chronicle) > 300:
            del profile.chronicle[:-300]
        return entry
