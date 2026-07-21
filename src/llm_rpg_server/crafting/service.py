from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.players import PlayerProfile, PlayerRepository
from llm_rpg_server.shared.config import ContentProvider

from .models import CraftAttempt, CraftDraft, CraftResult, ItemReference, RecipeRecord
from .repository import InMemoryRecipeRepository, recipe_key
from .workflow import CraftingWorkflow

STACKABLE_ITEM_TYPES = {"item", "material"}


class CraftingService:
    """Transactional facade around the LangGraph crafting workflow."""

    def __init__(
        self,
        players: PlayerRepository,
        catalog: Catalog,
        recipes: InMemoryRecipeRepository,
        content: ContentProvider,
        workflow: CraftingWorkflow,
    ):
        self.players = players
        self.catalog = catalog
        self.recipes = recipes
        self.content = content
        self.workflow = workflow
        self.generated_items = content.document("catalog/generated_items.json")

    def craft(self, player_id: str, first: ItemReference, second: ItemReference) -> CraftAttempt:
        started_at = time.perf_counter()
        profile = self.players.get(player_id)
        first_info = self._validate_reference(profile, first)
        second_info = self._validate_reference(profile, second)
        self._validate_pair(profile, first, second, first_info, second_info)
        key = recipe_key(first, second)
        cached = self.recipes.get(key)
        if cached:
            if not cached.success or cached.result is None:
                return CraftAttempt(
                    success=False,
                    failure_reason=cached.failure_reason,
                    duration_ms=self._elapsed_ms(started_at),
                    recipe_cached=True,
                )
            result = self._instantiate_cached_result(cached.result)
        else:
            record = self._evaluate(first, second, first_info, second_info)
            if not record.success or record.result is None:
                saved = self.recipes.save(key, record)
                return CraftAttempt(
                    success=False,
                    failure_reason=saved.failure_reason,
                    duration_ms=self._elapsed_ms(started_at),
                )
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
                    duration_ms=record.duration_ms,
                ),
            )
        self.catalog.register_generated(result.item_type, result.id, self._catalog_definition(result))
        return CraftAttempt(
            success=True,
            result=result,
            duration_ms=self._elapsed_ms(started_at),
            recipe_cached=cached is not None,
        )

    def _evaluate(
        self,
        first_reference: ItemReference,
        second_reference: ItemReference,
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> RecipeRecord:
        started_at = time.perf_counter()
        item_type = self._result_type(first_reference.item_type, second_reference.item_type)
        ingredients = self._ordered_pair(first_reference, second_reference)
        outcome = self.workflow.run(first, second, item_type)
        if not outcome.success or outcome.draft is None:
            return RecipeRecord(
                ingredients=ingredients,
                success=False,
                failure_reason=outcome.failure_reason,
                duration_ms=self._elapsed_ms(started_at),
            )
        result = self._result_from_draft(
            outcome.draft,
            ingredients,
            first_reference,
            second_reference,
            first,
            second,
        )
        return RecipeRecord(
            ingredients=ingredients,
            success=True,
            result=result,
            duration_ms=self._elapsed_ms(started_at),
        )

    def _result_from_draft(
        self,
        draft: CraftDraft,
        ingredients: tuple[ItemReference, ItemReference],
        first_reference: ItemReference,
        second_reference: ItemReference,
        first: dict[str, Any],
        second: dict[str, Any],
    ) -> CraftResult:
        item_type = draft.properties.item_type
        return CraftResult(
            id=self._result_item_id(item_type, ingredients),
            name=draft.concept.name.strip(),
            desc=draft.concept.desc.strip(),
            value=max(0, int(first.get("value", 0)) + int(second.get("value", 0))),
            item_type=item_type,
            combat_stat=self._combat_stat(item_type, draft.properties.properties),
            image_url=draft.artwork.image_url,
            image_key=draft.artwork.image_key,
            image_status=draft.artwork.status,
            can_be_ingredient=draft.properties.can_be_ingredient,
            tradable=draft.properties.tradable,
            use_contexts=draft.properties.use_contexts,
            category=draft.properties.category,
            tags=draft.properties.tags,
            properties=draft.properties.properties,
            ingredient_ancestry=self._ingredient_ancestry(
                first_reference,
                second_reference,
                first,
                second,
            ),
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, round((time.perf_counter() - started_at) * 1000))

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
        if any(
            (
                reference.item_type == "weapon"
                and reference.item_id == profile.equipped_weapon_id
            )
            or (
                reference.item_type == "armor"
                and reference.item_id == profile.equipped_armor_id
            )
            for reference in (first, second)
        ):
            raise ValueError(self.content.text("errors.craft.equipped"))
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
            "image_key": result.image_key,
            "image_status": result.image_status,
            "can_be_ingredient": result.can_be_ingredient,
            "tradable": result.tradable,
            "use_contexts": result.use_contexts,
            "category": result.category,
            "tags": result.tags,
            "_crafting_ancestry": [reference.model_dump(mode="json") for reference in result.ingredient_ancestry],
        }
        properties = result.properties
        if result.item_type == "weapon":
            weapon = self.generated_items["weapon"]
            common.update({
                "base_dmg": int(properties.get("base_dmg", result.combat_stat)),
                "range": str(properties.get("range", weapon["range"])),
                "type": str(properties.get("damage_type", weapon["damage_type"])),
                "skills": [dict(weapon["skill"])],
            })
        elif result.item_type == "armor":
            common.update({
                "hp_bonus": int(properties.get("hp_bonus", result.combat_stat)),
                "def_rate": float(properties.get("def_rate", self.generated_items["armor"]["def_rate"])),
            })
        elif result.item_type == "item":
            common.update({
                "type": str(properties.get("effect_type", self.generated_items["item"]["effect_type"])),
                "val": int(properties.get("val", result.combat_stat)),
                "stamina_restore": int(properties.get("stamina_restore", 0)),
                "clear_negative_statuses": bool(properties.get("clear_negative_statuses", False)),
            })
        else:
            common.update({"emoji": self.generated_items["material"]["emoji"]})
        return common

    @staticmethod
    def _combat_stat(item_type: str, properties: dict[str, Any]) -> int:
        if item_type == "weapon":
            return max(0, int(properties.get("base_dmg", 0)))
        if item_type == "armor":
            return max(0, int(properties.get("hp_bonus", 0)))
        if item_type == "item":
            return max(0, int(properties.get("val", 0)))
        return 0

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
