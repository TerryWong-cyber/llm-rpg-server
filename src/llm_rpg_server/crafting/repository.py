from __future__ import annotations

from copy import deepcopy
from threading import RLock

from .models import CraftResult, ItemReference

RecipeKey = tuple[str, str]


def recipe_key(first: ItemReference, second: ItemReference) -> RecipeKey:
    return tuple(sorted((f"{first.item_type}:{first.item_id}", f"{second.item_type}:{second.item_id}")))


class InMemoryRecipeRepository:
    def __init__(self):
        self._recipes: dict[RecipeKey, CraftResult] = {}
        self._lock = RLock()

    def get(self, key: RecipeKey) -> CraftResult | None:
        with self._lock:
            value = self._recipes.get(key)
            return deepcopy(value) if value is not None else None

    def save(self, key: RecipeKey, result: CraftResult) -> CraftResult:
        with self._lock:
            self._recipes.setdefault(key, deepcopy(result))
            return deepcopy(self._recipes[key])

    def list_all(self) -> list[dict]:
        with self._lock:
            values = []
            for key, result in self._recipes.items():
                first_type, first_id = key[0].split(":", 1)
                second_type, second_id = key[1].split(":", 1)
                values.append({
                    "ingredients": [
                        {"item_type": first_type, "item_id": first_id},
                        {"item_type": second_type, "item_id": second_id},
                    ],
                    "mat1_id": first_id,
                    "mat2_id": second_id,
                    "result": result.public_dict(),
                })
            return values
