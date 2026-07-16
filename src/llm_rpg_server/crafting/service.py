from __future__ import annotations

import hashlib
import uuid
from typing import Any

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import PlayerProfile, PlayerRepository
from llm_rpg_server.shared.config import ContentProvider

from .generators import CraftDecisionGenerator, ItemImageGenerator
from .models import CraftAttempt, CraftResult, ItemReference, RecipeRecord
from .repository import InMemoryRecipeRepository, recipe_key

STACKABLE_ITEM_TYPES = {"item", "material"}


class CraftingService:
    def __init__(
        self,
        players: PlayerRepository,
        catalog: Catalog,
        recipes: InMemoryRecipeRepository,
        content: ContentProvider,
        decision_generator: CraftDecisionGenerator,
        image_generator: ItemImageGenerator,
    ):
        self.players = players
        self.catalog = catalog
        self.recipes = recipes
        self.content = content
        self.decision_generator = decision_generator
        self.image_generator = image_generator
        self.generated_items = content.document("catalog/generated_items.json")

    def craft(self, player_id: str, first: ItemReference, second: ItemReference) -> CraftAttempt:
        profile = self.players.get(player_id)
        first_info = self._validate_reference(profile, first)
        second_info = self._validate_reference(profile, second)
        self._validate_pair(profile, first, second, first_info, second_info)
        key = recipe_key(first, second)
        cached = self.recipes.get(key)
        if cached:
            if not cached.success or cached.result is None:
                return CraftAttempt(success=False, failure_reason=cached.failure_reason)
            result = self._instantiate_cached_result(cached.result)
        else:
            record = self._evaluate(first, second, first_info, second_info)
            if not record.success or record.result is None:
                saved = self.recipes.save(key, record)
                return CraftAttempt(success=False, failure_reason=saved.failure_reason)
            result = record.result
        with self.players.transaction(player_id) as current:
            current_first = self._validate_reference(current, first)
            current_second = self._validate_reference(current, second)
            self._validate_pair(current, first, second, current_first, current_second)
            self._deduct(current, first)
            self._deduct(current, second)
            self._add_result(current, result)
        if cached is None:
            self.recipes.save(
                key,
                RecipeRecord(
                    ingredients=self._ordered_pair(first, second),
                    success=True,
                    result=result,
                ),
            )
        self.catalog.register_generated(result.item_type, result.id, self._catalog_definition(result))
        return CraftAttempt(success=True, result=result)

    def _evaluate(
        self,
        first_reference: ItemReference,
        second_reference: ItemReference,
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> RecipeRecord:
        item_type = self._result_type(first_reference.item_type, second_reference.item_type)
        ingredients = self._ordered_pair(first_reference, second_reference)
        decision = self.decision_generator.evaluate(first, second, item_type)
        if not decision.success:
            return RecipeRecord(
                ingredients=ingredients,
                success=False,
                failure_reason=decision.reason.strip(),
            )
        value = int(first.get("value", 0)) + int(second.get("value", 0))
        combat_stat = self._combat_stat(item_type, first, second)
        result = CraftResult(
            id=self._result_item_id(item_type, ingredients),
            name=decision.name.strip(),
            desc=decision.desc.strip(),
            value=value,
            item_type=item_type,
            combat_stat=combat_stat,
            can_be_ingredient=decision.can_be_ingredient,
            ingredient_ancestry=self._ingredient_ancestry(
                first_reference,
                second_reference,
                first,
                second,
            ),
        )
        result = result.model_copy(update={"image_url": self.image_generator.generate(result.name, result.desc)})
        return RecipeRecord(ingredients=ingredients, success=True, result=result)

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
        if not definition.get("can_be_ingredient", True):
            raise ValueError(self.content.text("errors.craft.ingredient_forbidden"))
        definition["_crafting_type"] = reference.item_type
        return definition

    def _validate_pair(
        self,
        profile: PlayerProfile,
        first: ItemReference,
        second: ItemReference,
        first_info: dict[str, Any],
        second_info: dict[str, Any],
    ) -> None:
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
        if first.identity in self._ancestry_identities(second_info) or second.identity in self._ancestry_identities(
            first_info
        ):
            raise ValueError(self.content.text("errors.craft.ancestor_forbidden"))

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
            "can_be_ingredient": result.can_be_ingredient,
            "_crafting_ancestry": [reference.model_dump(mode="json") for reference in result.ingredient_ancestry],
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
    def _new_item_id() -> str:
        return f"craft_{uuid.uuid4().hex[:8]}"

    def _result_item_id(
        self,
        item_type: str,
        ingredients: tuple[ItemReference, ItemReference],
    ) -> str:
        if item_type not in STACKABLE_ITEM_TYPES:
            return self._new_item_id()
        signature = "|".join(reference.identity for reference in ingredients)
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        return f"craft_stack_{digest}"

    def _instantiate_cached_result(self, result: CraftResult) -> CraftResult:
        if result.item_type in STACKABLE_ITEM_TYPES:
            return result.model_copy()
        return result.model_copy(update={"id": self._new_item_id()})

    @staticmethod
    def _ordered_pair(first: ItemReference, second: ItemReference) -> tuple[ItemReference, ItemReference]:
        return tuple(sorted((first, second), key=lambda reference: reference.identity))

    def _ingredient_ancestry(
        self,
        first_reference: ItemReference,
        second_reference: ItemReference,
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> list[ItemReference]:
        references = [first_reference, second_reference]
        references.extend(self._stored_ancestry(first))
        references.extend(self._stored_ancestry(second))
        unique = {reference.identity: reference for reference in references}
        return [unique[identity] for identity in sorted(unique)]

    def _ancestry_identities(self, definition: dict[str, Any]) -> set[str]:
        return {reference.identity for reference in self._stored_ancestry(definition)}

    @staticmethod
    def _stored_ancestry(definition: dict[str, Any]) -> list[ItemReference]:
        return [ItemReference.model_validate(value) for value in definition.get("_crafting_ancestry", [])]

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
