import pytest

from llm_rpg_server.crafting import CraftDecision, CraftingService, InMemoryRecipeRepository, ItemReference
from llm_rpg_server.players import InMemoryPlayerRepository, PlayerService


class DecisionGenerator:
    def __init__(self, decisions=None):
        self.decisions = list(decisions or [])
        self.calls = 0

    def evaluate(self, first, second, result_type):
        self.calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        return successful_decision()


class FailingDecisionGenerator:
    def evaluate(self, first, second, result_type):
        raise RuntimeError("generation failed")


class ImageGenerator:
    def generate(self, name, description):
        return "https://example.test/item.png"


def successful_decision(*, can_be_ingredient=True, name="Test Alloy"):
    return CraftDecision(
        success=True,
        reason="The material properties have a coherent transformation path.",
        name=name,
        desc="A controlled test result.",
        can_be_ingredient=can_be_ingredient,
    )


def failed_decision():
    return CraftDecision(
        success=False,
        reason="The ingredients have no compatible transformation path.",
    )


def build_service(content, catalog, generator=None):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Crafter", "1")
    recipes = InMemoryRecipeRepository()
    service = CraftingService(
        players,
        catalog,
        recipes,
        content,
        generator or DecisionGenerator(),
        ImageGenerator(),
    )
    return service, players, player, recipes


def add_items(players, player_id, **quantities):
    with players.transaction(player_id) as profile:
        for item_id, quantity in quantities.items():
            profile.inventory.items[item_id] = quantity


def craft_items(service, player_id, first_id, second_id):
    return service.craft(
        player_id,
        ItemReference(item_type="item", item_id=first_id),
        ItemReference(item_type="item", item_id=second_id),
    )


def test_crafting_commits_inventory_and_registers_ingredient_flag(content, catalog):
    service, players, player, _ = build_service(content, catalog)
    add_items(players, player.player_id, **{"2": 1})

    attempt = craft_items(service, player.player_id, "1", "2")

    assert attempt.success is True
    assert attempt.result is not None
    result = attempt.result
    profile = players.get(player.player_id)
    assert profile.inventory.items["1"] == 2
    assert profile.inventory.items["2"] == 0
    assert profile.inventory.items[result.id] == 1
    assert catalog.items[result.id]["value"] == result.value
    assert catalog.items[result.id]["can_be_ingredient"] is True
    assert result.public_dict()["can_be_ingredient"] is True
    assert "ingredient_ancestry" not in result.public_dict()


def test_same_recipe_stacks_materials_and_consumables_under_one_id(content, catalog):
    generator = DecisionGenerator()
    service, players, player, recipes = build_service(content, catalog, generator)
    add_items(players, player.player_id, **{"2": 2})

    first_attempt = craft_items(service, player.player_id, "1", "2")
    second_attempt = craft_items(service, player.player_id, "2", "1")

    assert first_attempt.result is not None
    assert second_attempt.result is not None
    assert first_attempt.result.id == second_attempt.result.id
    assert first_attempt.result.id.startswith("craft_stack_")
    assert players.get(player.player_id).inventory.items[first_attempt.result.id] == 2
    assert generator.calls == 1
    assert len(recipes.list_all()) == 1


def test_same_equipment_recipe_keeps_distinct_item_ids(content, catalog):
    generator = DecisionGenerator()
    service, players, player, _ = build_service(content, catalog, generator)
    weapon = ItemReference(item_type="weapon", item_id="2")
    with players.transaction(player.player_id) as profile:
        profile.inventory.weapons.extend(["2", "2"])

    first_attempt = service.craft(player.player_id, weapon, weapon)
    with players.transaction(player.player_id) as profile:
        profile.inventory.weapons.extend(["2", "2"])
    second_attempt = service.craft(player.player_id, weapon, weapon)

    assert first_attempt.result is not None
    assert second_attempt.result is not None
    assert first_attempt.result.id != second_attempt.result.id
    inventory = players.get(player.player_id).inventory.weapons
    assert first_attempt.result.id in inventory
    assert second_attempt.result.id in inventory
    assert generator.calls == 1


def test_failed_recipe_is_cached_globally_without_consuming_inputs(content, catalog):
    generator = DecisionGenerator([failed_decision()])
    service, players, player, recipes = build_service(content, catalog, generator)
    add_items(players, player.player_id, **{"2": 1})
    before = players.get(player.player_id).inventory.model_dump()

    first_attempt = craft_items(service, player.player_id, "1", "2")
    second_attempt = craft_items(service, player.player_id, "2", "1")

    assert first_attempt.success is False
    assert second_attempt.success is False
    assert second_attempt.failure_reason == first_attempt.failure_reason
    assert generator.calls == 1
    assert players.get(player.player_id).inventory.model_dump() == before
    records = recipes.list_all()
    assert len(records) == 1
    assert records[0]["success"] is False
    assert records[0]["result"] is None
    assert records[0]["failure_reason"] == first_attempt.failure_reason


def test_completed_result_cannot_be_reused_as_an_ingredient(content, catalog):
    generator = DecisionGenerator([successful_decision(can_be_ingredient=False)])
    service, players, player, _ = build_service(content, catalog, generator)
    add_items(players, player.player_id, **{"2": 1})
    attempt = craft_items(service, player.player_id, "1", "2")
    assert attempt.result is not None

    with pytest.raises(ValueError, match="完整成品"):
        craft_items(service, player.player_id, attempt.result.id, "1")

    assert generator.calls == 1


def test_result_cannot_combine_with_direct_or_recursive_ancestors(content, catalog):
    generator = DecisionGenerator([
        successful_decision(name="First Compound"),
        successful_decision(name="Second Compound"),
    ])
    service, players, player, _ = build_service(content, catalog, generator)
    add_items(players, player.player_id, **{"2": 1, "3": 1})

    first_attempt = craft_items(service, player.player_id, "1", "2")
    assert first_attempt.result is not None
    first_result = first_attempt.result
    with pytest.raises(ValueError, match="任一级原材料"):
        craft_items(service, player.player_id, first_result.id, "1")

    second_attempt = craft_items(service, player.player_id, first_result.id, "3")
    assert second_attempt.result is not None
    second_result = second_attempt.result
    with pytest.raises(ValueError, match="任一级原材料"):
        craft_items(service, player.player_id, second_result.id, "1")

    assert generator.calls == 2


def test_generation_failure_does_not_consume_inputs(content, catalog):
    service, players, player, _ = build_service(content, catalog, FailingDecisionGenerator())
    before = players.get(player.player_id).inventory.items["1"]

    with pytest.raises(RuntimeError):
        craft_items(service, player.player_id, "1", "1")

    assert players.get(player.player_id).inventory.items["1"] == before
