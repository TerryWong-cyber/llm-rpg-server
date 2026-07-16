from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

from llm_rpg_server.shared.config import ContentProvider


class Catalog:
    def __init__(self, content: ContentProvider):
        payload = content.document("catalog/game.json")
        self.characters = payload["characters"]
        self.weapons = payload["weapons"]
        self.armors = payload["armors"]
        self.items = payload["items"]
        self.resources = payload["resources"]
        self.environments = payload["environments"]
        self.rules = payload["rules"]
        self._lock = RLock()
        self._apply_crafting_defaults()
        self._validate()

    def public_view(self) -> dict[str, Any]:
        with self._lock:
            return {
                "characters": self._public_collection(self.characters),
                "weapons": self._public_collection(self.weapons),
                "armors": self._public_collection(self.armors),
                "items": self._public_collection(self.items),
                "resources": self._public_collection(self.resources),
            }

    def register_generated(self, item_type: str, item_id: str, definition: dict[str, Any]) -> None:
        collection = {
            "weapon": self.weapons,
            "armor": self.armors,
            "item": self.items,
            "material": self.resources,
        }[item_type]
        with self._lock:
            collection[item_id] = deepcopy(definition)

    def item_definition(self, item_type: str, item_id: str) -> dict[str, Any] | None:
        collection = {
            "weapon": self.weapons,
            "armor": self.armors,
            "item": self.items,
            "material": self.resources,
        }.get(item_type)
        if collection is None:
            return None
        with self._lock:
            value = collection.get(item_id)
            return deepcopy(value) if value is not None else None

    def _apply_crafting_defaults(self) -> None:
        for collection in (self.weapons, self.armors, self.items, self.resources):
            for definition in collection.values():
                definition.setdefault("can_be_ingredient", True)

    @staticmethod
    def _public_collection(collection: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return deepcopy({
            item_id: {key: value for key, value in definition.items() if not key.startswith("_")}
            for item_id, definition in collection.items()
        })

    def _validate(self) -> None:
        collections = {
            "characters": self.characters,
            "weapons": self.weapons,
            "armors": self.armors,
            "items": self.items,
            "resources": self.resources,
        }
        for name, collection in collections.items():
            for item_id, definition in collection.items():
                if str(definition.get("id")) != item_id:
                    raise ValueError(f"Catalog {name} key {item_id} does not match its id field")
        for weapon_id, weapon in self.weapons.items():
            skill_ids = [skill["id"] for skill in weapon.get("skills", [])]
            if len(skill_ids) != len(set(skill_ids)):
                raise ValueError(f"Weapon {weapon_id} contains duplicate skill ids")
        required_rules = {
            "phys_attr_coeff",
            "magic_attr_coeff",
            "status_dmg_per_turn",
            "normal_effectiveness",
            "defended_effectiveness",
            "min_gold_drop",
            "max_gold_drop",
            "initial_gold",
        }
        missing_rules = required_rules - set(self.rules)
        if missing_rules:
            raise ValueError(f"Catalog is missing required rules: {sorted(missing_rules)}")
