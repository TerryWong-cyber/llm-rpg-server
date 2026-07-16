from __future__ import annotations

import uuid
from typing import Any

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import PlayerProfile, PlayerRepository
from llm_rpg_server.shared.config import ContentProvider

from .generators import CraftNarrativeGenerator, ItemImageGenerator
from .models import CraftResult, ItemReference
from .repository import InMemoryRecipeRepository, recipe_key


class CraftingService:
    def __init__(
        self,
        players: PlayerRepository,
        catalog: Catalog,
        recipes: InMemoryRecipeRepository,
        content: ContentProvider,
        narrative_generator: CraftNarrativeGenerator,
        image_generator: ItemImageGenerator,
    ):
        self.players = players
        self.catalog = catalog
        self.recipes = recipes
        self.content = content
        self.narrative_generator = narrative_generator
        self.image_generator = image_generator
        self.generated_items = content.document("catalog/generated_items.json")

    def craft(self, player_id: str, first: ItemReference, second: ItemReference) -> CraftResult:
        profile = self.players.get(player_id)
        first_info = self._validate_reference(profile, first)
        second_info = self._validate_reference(profile, second)
        self._validate_pair(profile, first, second)
        key = recipe_key(first, second)
        cached = self.recipes.get(key)
        if cached:
            result = cached.model_copy(update={"id": f"craft_{uuid.uuid4().hex[:8]}"})
        else:
            result = self._generate(first, second, first_info, second_info)
            result = result.model_copy(update={
                "id": f"craft_{uuid.uuid4().hex[:8]}",
                "image_url": self.image_generator.generate(result.name, result.desc),
            })
        with self.players.transaction(player_id) as current:
            self._validate_reference(current, first)
            self._validate_reference(current, second)
            self._validate_pair(current, first, second)
            self._deduct(current, first)
            self._deduct(current, second)
            self._add_result(current, result)
        if cached is None:
            self.recipes.save(key, result.model_copy(update={"id": ""}))
        self.catalog.register_generated(result.item_type, result.id, self._catalog_definition(result))
        return result

    def _generate(
        self,
        first_reference: ItemReference,
        second_reference: ItemReference,
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> CraftResult:
        narrative = self.narrative_generator.generate(first, second)
        item_type = self._result_type(first_reference.item_type, second_reference.item_type)
        value = int(first.get("value", 0)) + int(second.get("value", 0))
        combat_stat = self._combat_stat(item_type, first, second)
        return CraftResult(
            name=narrative.name[:40],
            desc=narrative.desc[:50],
            value=value,
            item_type=item_type,
            combat_stat=combat_stat,
        )

    def _validate_reference(self, profile: PlayerProfile, reference: ItemReference) -> dict[str, Any]:
        inventory = profile.inventory
        quantities = {
            "weapon": inventory.weapons.count(reference.item_id),
            "armor": inventory.armors.count(reference.item_id),
            "item": inventory.items.get(reference.item_id, 0),
            "material": inventory.materials.get(reference.item_id, 0),
        }
        definition = self.catalog.item_definition(reference.item_type, reference.item_id)
        if quantities[reference.item_type] <= 0 or definition is None:
            raise ValueError(self.content.text("errors.inventory.insufficient"))
        return definition

    def _validate_pair(self, profile: PlayerProfile, first: ItemReference, second: ItemReference) -> None:
        inventory = profile.inventory
        if first == second:
            quantities = {
                "weapon": inventory.weapons.count(first.item_id),
                "armor": inventory.armors.count(first.item_id),
                "item": inventory.items.get(first.item_id, 0),
                "material": inventory.materials.get(first.item_id, 0),
            }
            if quantities[first.item_type] < 2:
                raise ValueError(self.content.text("errors.inventory.insufficient"))
        consumed_weapons = int(first.item_type == "weapon") + int(second.item_type == "weapon")
        consumed_armors = int(first.item_type == "armor") + int(second.item_type == "armor")
        if len(inventory.weapons) - consumed_weapons < 1:
            raise ValueError(self.content.text("errors.inventory.keep_weapon"))
        if len(inventory.armors) - consumed_armors < 1:
            raise ValueError(self.content.text("errors.inventory.keep_armor"))

    @staticmethod
    def _deduct(profile: PlayerProfile, reference: ItemReference) -> None:
        inventory = profile.inventory
        if reference.item_type == "weapon":
            inventory.weapons.remove(reference.item_id)
        elif reference.item_type == "armor":
            inventory.armors.remove(reference.item_id)
        elif reference.item_type == "item":
            inventory.items[reference.item_id] -= 1
        else:
            inventory.materials[reference.item_id] -= 1
            if inventory.materials[reference.item_id] == 0:
                del inventory.materials[reference.item_id]

    @staticmethod
    def _add_result(profile: PlayerProfile, result: CraftResult) -> None:
        inventory = profile.inventory
        if result.item_type == "weapon":
            inventory.weapons.append(result.id)
        elif result.item_type == "armor":
            inventory.armors.append(result.id)
        elif result.item_type == "item":
            inventory.items[result.id] = inventory.items.get(result.id, 0) + 1
        else:
            inventory.materials[result.id] = inventory.materials.get(result.id, 0) + 1

    def _catalog_definition(self, result: CraftResult) -> dict[str, Any]:
        common = {
            "id": result.id,
            "name": result.name,
            "value": result.value,
            "desc": result.desc,
            "image_url": result.image_url,
        }
        if result.item_type == "weapon":
            weapon = self.generated_items["weapon"]
            common.update({
                "base_dmg": result.combat_stat,
                "range": weapon["range"],
                "type": weapon["damage_type"],
                "skills": [dict(weapon["skill"])],
            })
        elif result.item_type == "armor":
            common.update({
                "hp_bonus": result.combat_stat,
                "def_rate": self.generated_items["armor"]["def_rate"],
            })
        elif result.item_type == "item":
            common.update({
                "type": self.generated_items["item"]["effect_type"],
                "val": result.combat_stat,
            })
        else:
            common.update({"emoji": self.generated_items["material"]["emoji"]})
        return common

    @staticmethod
    def _result_type(first: str, second: str) -> str:
        if first == second:
            return first
        types = {first, second}
        if "material" in types:
            return next(item for item in types if item != "material")
        if "item" in types:
            return "item"
        return "material"

    def _combat_stat(self, item_type: str, first: dict[str, Any], second: dict[str, Any]) -> int:
        if item_type == "material":
            return 0
        source_keys = {"weapon": "base_dmg", "armor": "hp_bonus", "item": "val"}
        limits = self.generated_items["combat_stat_limits"]
        key = source_keys[item_type]
        values = [int(item.get(key, 0)) for item in (first, second) if item.get(key) is not None]
        estimate = sum(values) if values else limits[item_type][0]
        lower, upper = (int(value) for value in limits[item_type])
        return max(lower, min(upper, estimate))
