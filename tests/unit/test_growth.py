from __future__ import annotations

from llm_rpg_server.bootstrap import build_container
from llm_rpg_server.combat import CombatRulebook
from llm_rpg_server.exploration import ExplorationService
from llm_rpg_server.players import GrowthService, InMemoryPlayerRepository, PlayerService


def test_player_can_equip_and_unequip_owned_gear(content, catalog):
    players = InMemoryPlayerRepository()
    service = PlayerService(players, catalog, content)
    player = service.create("装备测试者", "1")
    with players.transaction(player.player_id) as profile:
        profile.inventory.weapons.append("2")
        profile.inventory.armors.append("2")
    equipped = service.set_equipment(player.player_id, "weapon", "2")
    assert equipped.equipped_weapon_id == "2"
    equipped = service.set_equipment(player.player_id, "armor", "2")
    assert equipped.equipped_armor_id == "2"
    unequipped = service.set_equipment(player.player_id, "weapon", None)
    assert unequipped.equipped_weapon_id is None


def test_players_start_at_level_one_with_race_attributes(content, catalog):
    players = InMemoryPlayerRepository()
    service = PlayerService(players, catalog, content)

    human = service.create("Human", "1")
    vampire = service.create("Vampire", "6")

    assert human.level == vampire.level == 1
    assert human.experience == vampire.experience == 0
    assert human.attribute_points == vampire.attribute_points == 0
    assert human.race_id == "1"
    assert vampire.race_id == "6"
    assert abs(sum(human.attributes.model_dump().values()) - sum(vampire.attributes.model_dump().values())) <= 1


def test_level_up_grants_five_points_and_allocation_updates_resources(content, catalog):
    players = InMemoryPlayerRepository()
    player_service = PlayerService(players, catalog, content)
    growth = GrowthService(players, player_service, content)
    profile = player_service.create("Grower", "1")

    awarded, result = growth.award_once(
        profile.player_id,
        growth.experience_to_next(1),
        "test:first-level",
    )
    leveled = players.get(profile.player_id)

    assert awarded is True
    assert result == {"experience": 100, "levels_gained": 1, "level": 2, "attribute_points": 5}
    assert leveled.level == 2
    assert leveled.attribute_points == 5
    assert leveled.experience_to_next > 100

    old_hp = leveled.max_hp
    allocated = growth.allocate(profile.player_id, {"vitality": 3, "strength": 2})
    assert allocated.attribute_points == 0
    assert allocated.attributes.vitality == profile.attributes.vitality + 3
    assert allocated.attributes.strength == profile.attributes.strength + 2
    assert allocated.max_hp > old_hp


def test_race_birthplaces_are_safe_towns_with_shop_and_inn(content, catalog):
    players = InMemoryPlayerRepository()
    player_service = PlayerService(players, catalog, content)
    exploration = ExplorationService(players, catalog, content)

    for race_id, race in catalog.races.items():
        profile = player_service.create(f"Race-{race_id}", race_id)
        current, _ = exploration.enter(profile.player_id, seed=42)
        cell = current.cells[current.current_cell_id]
        assert current.template_id == race["birthplace"]["template_id"]
        assert current.region_id == race["birthplace"]["region_id"]
        assert current.current_cell_id == race["birthplace"]["cell_id"]
        assert cell.landmark_id == race["birthplace"]["settlement_name"]
        assert {"shop", "inn"} <= set(cell.interaction_ids)


def test_vampire_is_stronger_at_night_than_in_sunlight(content, catalog):
    players = InMemoryPlayerRepository()
    player_service = PlayerService(players, catalog, content)
    vampire = player_service.create("Nightborn", "6")
    character = player_service.combat_character(vampire)
    rules = CombatRulebook(content)
    weapon = catalog.weapons["1"]
    armor = catalog.armors["0"]

    night = rules.derive_stats(character, weapon, armor, environment_tags=["night", "dark"])
    sunlight = rules.derive_stats(character, weapon, armor, environment_tags=["day", "sunlight"])

    assert night.accuracy > sunlight.accuracy
    assert night.evasion > sunlight.evasion
    assert character["passives"]["life_steal"] == 0.12


def test_quest_completion_consumes_objectives_and_awards_experience():
    container = build_container()
    profile = container.player_service.create("Questor", "1")
    hook = container.npc_interactions.activate_story_hook(
        "evelyn_mossmark",
        profile.player_id,
        "evelyn_lost_spores",
        source="test",
    )
    with container.players.transaction(profile.player_id) as editable:
        editable.inventory.materials["mat_3"] = 2

    reward = container.growth.complete_quest(
        profile.player_id,
        "evelyn_mossmark",
        hook.hook_id,
    )
    container.npc_interactions.complete_story_hook(
        "evelyn_mossmark",
        profile.player_id,
        hook.hook_id,
    )
    completed = container.players.get(profile.player_id)

    assert reward["experience"] == 120
    assert completed.level == 2
    assert completed.attribute_points == 5
    assert completed.inventory.materials.get("mat_3", 0) == 0
    assert hook.hook_id in completed.completed_quests


def test_random_pve_reward_includes_experience():
    container = build_container()
    profile = container.player_service.create("Fighter", "1")
    with container.players.transaction(profile.player_id) as editable:
        reward = container.combat.engine._grant_reward(editable)

    persisted = container.players.get(profile.player_id)
    assert reward["experience"] == container.growth.rules.experience_rewards.random_pve
    assert persisted.total_experience == reward["experience"]
