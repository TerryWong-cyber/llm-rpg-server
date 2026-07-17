from __future__ import annotations

from copy import deepcopy

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.shared.config import ContentProvider

from .models import MonsterDefinition


class MonsterCatalog:
    def __init__(self, content: ContentProvider, catalog: Catalog):
        payload = content.document("monsters/world_monsters.json")
        values = [MonsterDefinition.model_validate(item) for item in payload["monsters"]]
        self._monsters = {item.monster_id: item for item in values}
        if len(self._monsters) != len(values):
            raise ValueError("Monster ids must be unique")
        self._validate_references(catalog)

    def get(self, monster_id: str) -> MonsterDefinition:
        try:
            return deepcopy(self._monsters[monster_id])
        except KeyError as exc:
            raise KeyError(monster_id) from exc

    def list_all(self) -> list[MonsterDefinition]:
        return deepcopy(list(self._monsters.values()))

    def public_view(self, monster_id: str) -> dict:
        return self.get(monster_id).public_view()

    def _validate_references(self, catalog: Catalog) -> None:
        for monster in self._monsters.values():
            equipment = monster.equipment
            if equipment.weapon_id not in catalog.weapons:
                raise ValueError(f"Monster {monster.monster_id} references unknown weapon")
            if equipment.armor_id not in catalog.armors:
                raise ValueError(f"Monster {monster.monster_id} references unknown armor")
            if equipment.item_id is not None and equipment.item_id not in catalog.items:
                raise ValueError(f"Monster {monster.monster_id} references unknown item")
            for drop in monster.drops:
                collection = catalog.items if drop.item_type == "item" else catalog.resources
                if drop.item_id not in collection:
                    raise ValueError(
                        f"Monster {monster.monster_id} references unknown drop {drop.item_id}"
                    )
