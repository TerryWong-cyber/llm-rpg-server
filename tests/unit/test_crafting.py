import pytest

from llm_rpg_server.crafting import CraftingService, InMemoryRecipeRepository, ItemReference
from llm_rpg_server.crafting.models import CraftNarrative
from llm_rpg_server.players import InMemoryPlayerRepository, PlayerService


class NarrativeGenerator:
    def generate(self, first, second):
        return CraftNarrative(name="Test Alloy", desc="A controlled test result.")


class FailingNarrativeGenerator:
    def generate(self, first, second):
        raise RuntimeError("generation failed")


class ImageGenerator:
    def generate(self, name, description):
        return "https://example.test/item.png"


def test_crafting_commits_inventory_after_generation(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Crafter", "1")
    with players.transaction(player.player_id) as profile:
        profile.inventory.items["2"] = 1
    service = CraftingService(
        players,
        catalog,
        InMemoryRecipeRepository(),
        content,
        NarrativeGenerator(),
        ImageGenerator(),
    )
    result = service.craft(
        player.player_id,
        ItemReference(item_type="item", item_id="1"),
        ItemReference(item_type="item", item_id="2"),
    )
    profile = players.get(player.player_id)
    assert profile.inventory.items["1"] == 2
    assert profile.inventory.items["2"] == 0
    assert profile.inventory.items[result.id] == 1
    assert catalog.items[result.id]["value"] == result.value


def test_generation_failure_does_not_consume_inputs(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Crafter", "1")
    before = players.get(player.player_id).inventory.items["1"]
    service = CraftingService(
        players,
        catalog,
        InMemoryRecipeRepository(),
        content,
        FailingNarrativeGenerator(),
        ImageGenerator(),
    )
    with pytest.raises(RuntimeError):
        service.craft(
            player.player_id,
            ItemReference(item_type="item", item_id="1"),
            ItemReference(item_type="item", item_id="1"),
        )
    assert players.get(player.player_id).inventory.items["1"] == before

