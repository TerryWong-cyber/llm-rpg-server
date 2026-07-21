import pytest

from llm_rpg_server.crafting import (
    CraftArtwork,
    CraftCategoryCatalog,
    CraftConcept,
    CraftingService,
    CraftingWorkflow,
    CraftPropertyProposal,
    InMemoryRecipeRepository,
    ItemReference,
)
from llm_rpg_server.players import InMemoryPlayerRepository, PlayerService


class ConceptGenerator:
    def __init__(self, concepts=None):
        self.concepts = list(concepts or [])
        self.calls = 0

    def generate(self, first, second, result_type, allowed_categories):
        self.calls += 1
        if self.concepts:
            return self.concepts.pop(0)
        return successful_concept(category="potion" if "potion" in allowed_categories else allowed_categories[0])


class FailingConceptGenerator:
    def generate(self, first, second, result_type, allowed_categories):
        raise RuntimeError("generation failed")


class PropertyGenerator:
    def __init__(self, proposals=None):
        self.proposals = list(proposals or [])
        self.calls = 0

    def generate(self, first, second, concept, property_contract, prompt_id):
        self.calls += 1
        if self.proposals:
            return self.proposals.pop(0)
        return default_properties(concept.category)


class ArtworkGenerator:
    def __init__(self, status="generated"):
        self.status = status
        self.calls = 0

    def generate(self, first, second, concept):
        self.calls += 1
        return CraftArtwork(image_key="outputs/crafting/test.png", status=self.status)


def successful_concept(*, category="potion", name="Test Alloy"):
    return CraftConcept(
        success=True,
        reason="The material properties have a coherent transformation path.",
        name=name,
        desc="A controlled test result.",
        category=category,
    )


def failed_concept():
    return CraftConcept(
        success=False,
        reason="The ingredients have no compatible transformation path.",
    )


def default_properties(category):
    values = {
        "weapon": {"base_dmg": 24, "damage_type": "phys", "range": "近战"},
        "armor": {"hp_bonus": 30, "def_rate": 0.2},
        "potion": {"effect_type": "heal_hp", "val": 42, "stamina_restore": 0, "clear_negative_statuses": False},
        "food": {"effect_type": "heal_hp", "val": 25, "stamina_restore": 30, "clear_negative_statuses": False},
        "bomb": {"effect_type": "dmg", "val": 45, "stamina_restore": 0, "clear_negative_statuses": False},
        "utility": {"effect_type": "heal_hp", "val": 0, "stamina_restore": 10, "clear_negative_statuses": False},
    }
    return CraftPropertyProposal(can_be_ingredient=True, properties=values.get(category, {}))


def build_service(content, catalog, concepts=None, proposals=None, artwork_status="generated"):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Crafter", "1")
    recipes = InMemoryRecipeRepository()
    concept_generator = concepts or ConceptGenerator()
    property_generator = PropertyGenerator(proposals)
    artwork_generator = ArtworkGenerator(artwork_status)
    workflow = CraftingWorkflow(
        CraftCategoryCatalog(content),
        concept_generator,
        artwork_generator,
        property_generator,
    )
    service = CraftingService(players, catalog, recipes, content, workflow)
    return service, players, player, recipes, concept_generator, property_generator, artwork_generator


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


def test_crafting_commits_inventory_registers_properties_and_oss_key(content, catalog):
    service, players, player, _, _, properties, artwork = build_service(content, catalog)
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
    assert catalog.items[result.id]["image_key"] == "outputs/crafting/test.png"
    assert result.public_dict()["image_status"] == "generated"
    assert result.properties["val"] == 42
    assert "ingredient_ancestry" not in result.public_dict()
    assert properties.calls == artwork.calls == 1


def test_parallel_nodes_use_category_properties_and_fallback_artwork(content, catalog):
    concepts = ConceptGenerator([successful_concept(category="potion")])
    service, players, player, _, _, properties, artwork = build_service(
        content,
        catalog,
        concepts=concepts,
        proposals=[CraftPropertyProposal(properties={"effect_type": "heal_mp", "val": 999, "stamina_restore": 2})],
        artwork_status="fallback",
    )
    add_items(players, player.player_id, **{"2": 1})

    attempt = craft_items(service, player.player_id, "1", "2")

    assert attempt.result is not None
    assert attempt.result.image_status == "fallback"
    assert attempt.result.properties["effect_type"] == "heal_mp"
    assert attempt.result.properties["val"] == 100
    assert attempt.result.combat_stat == 100
    assert properties.calls == artwork.calls == 1


def test_same_recipe_stacks_materials_and_consumables_under_one_id(content, catalog):
    concepts = ConceptGenerator()
    service, players, player, recipes, _, _, _ = build_service(content, catalog, concepts=concepts)
    add_items(players, player.player_id, **{"2": 2})

    first_attempt = craft_items(service, player.player_id, "1", "2")
    second_attempt = craft_items(service, player.player_id, "2", "1")

    assert first_attempt.result is not None
    assert second_attempt.result is not None
    assert first_attempt.result.id == second_attempt.result.id
    assert first_attempt.result.id.startswith("craft_stack_")
    assert players.get(player.player_id).inventory.items[first_attempt.result.id] == 2
    assert concepts.calls == 1
    assert len(recipes.list_all()) == 1
    assert first_attempt.recipe_cached is False
    assert second_attempt.recipe_cached is True


def test_same_equipment_recipe_keeps_distinct_item_ids(content, catalog):
    concepts = ConceptGenerator([successful_concept(category="weapon")])
    service, players, player, _, _, _, _ = build_service(content, catalog, concepts=concepts)
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
    assert concepts.calls == 1


def test_failed_recipe_is_cached_globally_without_consuming_inputs(content, catalog):
    concepts = ConceptGenerator([failed_concept()])
    service, players, player, recipes, _, _, _ = build_service(content, catalog, concepts=concepts)
    add_items(players, player.player_id, **{"2": 1})
    before = players.get(player.player_id).inventory.model_dump()

    first_attempt = craft_items(service, player.player_id, "1", "2")
    second_attempt = craft_items(service, player.player_id, "2", "1")

    assert first_attempt.success is False
    assert second_attempt.success is False
    assert second_attempt.failure_reason == first_attempt.failure_reason
    assert concepts.calls == 1
    assert players.get(player.player_id).inventory.model_dump() == before
    records = recipes.list_all()
    assert len(records) == 1
    assert records[0]["success"] is False
    assert records[0]["result"] is None
    assert records[0]["failure_reason"] == first_attempt.failure_reason
    assert records[0]["duration_ms"] >= 0
    assert first_attempt.recipe_cached is False
    assert second_attempt.recipe_cached is True


def test_crafting_records_attempt_and_first_discovery_duration(content, catalog, monkeypatch):
    ticks = iter([10.0, 10.01, 10.04, 10.05])
    monkeypatch.setattr(
        "llm_rpg_server.crafting.service.time.perf_counter",
        lambda: next(ticks),
    )
    service, players, player, recipes, _, _, _ = build_service(content, catalog)
    add_items(players, player.player_id, **{"2": 1})

    attempt = craft_items(service, player.player_id, "1", "2")

    assert attempt.duration_ms == 50
    assert attempt.recipe_cached is False
    assert recipes.list_all()[0]["duration_ms"] == 30


def test_completed_result_cannot_be_reused_as_an_ingredient(content, catalog):
    service, players, player, _, _, _, _ = build_service(
        content,
        catalog,
        proposals=[CraftPropertyProposal(can_be_ingredient=False, properties={
            "effect_type": "heal_hp", "val": 42, "stamina_restore": 0, "clear_negative_statuses": False,
        })],
    )
    add_items(players, player.player_id, **{"2": 1})
    attempt = craft_items(service, player.player_id, "1", "2")
    assert attempt.result is not None

    with pytest.raises(ValueError, match="完整成品"):
        craft_items(service, player.player_id, attempt.result.id, "1")


def test_result_cannot_combine_with_direct_or_recursive_ancestors(content, catalog):
    concepts = ConceptGenerator([
        successful_concept(name="First Compound"),
        successful_concept(name="Second Compound"),
    ])
    service, players, player, _, _, _, _ = build_service(content, catalog, concepts=concepts)
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

    assert concepts.calls == 2


def test_generation_failure_does_not_consume_inputs(content, catalog):
    service, players, player, _, _, _, _ = build_service(content, catalog, concepts=FailingConceptGenerator())
    before = players.get(player.player_id).inventory.items["1"]

    with pytest.raises(RuntimeError):
        craft_items(service, player.player_id, "1", "1")

    assert players.get(player.player_id).inventory.items["1"] == before
