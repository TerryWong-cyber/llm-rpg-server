from __future__ import annotations

import uuid

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.shared.config import ContentProvider

from .models import Inventory, PlayerProfile
from .repository import PlayerRepository


class PlayerService:
    def __init__(self, repository: PlayerRepository, catalog: Catalog, content: ContentProvider):
        self.repository = repository
        self.catalog = catalog
        self.content = content

    def create(self, name: str, character_id: str) -> PlayerProfile:
        if character_id not in self.catalog.characters:
            raise ValueError(self.content.text("errors.character.invalid"))
        defaults = self.content.document("catalog/default_player.json")
        profile = PlayerProfile(
            player_id=f"char_{uuid.uuid4().hex[:8]}",
            name=name,
            character_id=character_id,
            gold=int(self.catalog.rules["initial_gold"]),
            inventory=Inventory.model_validate(defaults["inventory"]),
        )
        return self.repository.create(profile)

    def ensure(self, player_id: str, name: str | None = None, character_id: str | None = None) -> PlayerProfile:
        if self.repository.exists(player_id):
            return self.repository.get(player_id)
        defaults = self.content.document("catalog/default_player.json")
        return self.repository.create(PlayerProfile(
            player_id=player_id,
            name=name or defaults["fallback_name"],
            character_id=character_id or defaults["character_id"],
            gold=int(self.catalog.rules["initial_gold"]),
            inventory=Inventory.model_validate(defaults["inventory"]),
        ))
