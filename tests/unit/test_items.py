import pytest

from llm_rpg_server.items import ItemService
from llm_rpg_server.players import InMemoryPlayerRepository, PlayerService


def make_service(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Item Tester", "1")
    return players, player, ItemService(players, catalog, content)


def test_use_outside_combat_applies_effect_and_consumes(content, catalog):
    players, player, service = make_service(content, catalog)
    with players.transaction(player.player_id) as profile:
        profile.current_hp -= 25
    profile, outcome = service.use_outside_combat(player.player_id, "1")
    assert outcome.hp_restored == 25
    assert profile.inventory.items["1"] == 2


def test_context_rejection_does_not_consume_item(content, catalog):
    players, player, service = make_service(content, catalog)
    with pytest.raises(ValueError):
        service.use_outside_combat(player.player_id, "8")
    assert players.get(player.player_id).inventory.items["8"] == 1


def test_food_restores_resources_and_applies_status(content, catalog):
    players, player, service = make_service(content, catalog)
    with players.transaction(player.player_id) as profile:
        profile.current_hp -= 10
        profile.stamina -= 20
    profile, outcome = service.use_outside_combat(player.player_id, "10")
    assert outcome.hp_restored == 4
    assert outcome.stamina_restored == 15
    assert outcome.applied_statuses == ["饱食"]
    assert profile.inventory.items["10"] == 4
    assert [status.status_id for status in profile.combat_statuses] == ["well_fed"]


def test_event_options_match_category_and_capability_tags(content, catalog):
    _, player, service = make_service(content, catalog)
    light_options = service.event_options(player.player_id, {
        "categories": ["tool", "bomb"],
        "any_tags": ["light_source", "fire"],
    })
    climbing_options = service.event_options(player.player_id, {
        "categories": ["tool"],
        "any_tags": ["climbing", "bridge"],
    })
    assert [option.item_id for option in light_options] == ["8"]
    assert [option.item_id for option in climbing_options] == ["9"]


def test_event_use_is_server_validated_and_consumed(content, catalog):
    players, player, service = make_service(content, catalog)
    with pytest.raises(ValueError):
        service.consume_for_event(player.player_id, "9", {"any_tags": ["light_source"]})
    assert players.get(player.player_id).inventory.items["9"] == 1
    outcome = service.consume_for_event(player.player_id, "9", {"any_tags": ["climbing"]})
    assert outcome.context == "world_event"
    assert "9" not in players.get(player.player_id).inventory.items
