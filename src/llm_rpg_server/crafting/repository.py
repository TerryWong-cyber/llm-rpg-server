from __future__ import annotations

from copy import deepcopy
from threading import RLock

from .models import ItemReference, RecipeRecord

RecipeKey = tuple[str, str]


def recipe_key(first: ItemReference, second: ItemReference) -> RecipeKey:
    return tuple(sorted((f"{first.item_type}:{first.item_id}", f"{second.item_type}:{second.item_id}")))


class InMemoryRecipeRepository:
    def __init__(self):
        self._recipes: dict[RecipeKey, RecipeRecord] = {}
        self._lock = RLock()

    def get(self, key: RecipeKey) -> RecipeRecord | None:
        with self._lock:
            value = self._recipes.get(key)
            return deepcopy(value) if value is not None else None

    def save(self, key: RecipeKey, record: RecipeRecord) -> RecipeRecord:
        with self._lock:
            self._recipes.setdefault(key, deepcopy(record))
            return deepcopy(self._recipes[key])

    def list_all(self) -> list[dict]:
        with self._lock:
            values = []
            for record in self._recipes.values():
                values.append(record.public_dict())
            return values
