from datetime import datetime, timedelta, timezone

import pytest

from llm_rpg_server.exploration import ExplorationService
from llm_rpg_server.exploration.models import MapInstance
from llm_rpg_server.players import InMemoryPlayerRepository, PlayerService
from llm_rpg_server.world import WorldClock


def create_service(content, catalog, *, now: datetime | None = None):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Explorer", "1")
    clock = None
    if now is not None:
        clock = WorldClock(content.document("maps/world.json")["time"], lambda: now)
    return players, player, ExplorationService(players, catalog, content, clock=clock)


def create_controlled_service(content, catalog, *, game_hour: int = 0):
    definition = content.document("maps/world.json")["time"]
    epoch = datetime.fromisoformat(definition["epoch_utc"].replace("Z", "+00:00"))
    now = {
        "value": epoch + timedelta(
            seconds=game_hour * float(definition["real_seconds_per_game_hour"])
        )
    }
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Event Explorer", "1")
    clock = WorldClock(definition, lambda: now["value"])
    service = ExplorationService(players, catalog, content, clock=clock)
    return players, player, service, now


def resolve_cell_event(service, players, player_id: str, cell_id: int):
    with players.transaction(player_id) as profile:
        current = MapInstance.model_validate(profile.current_map)
        service._hydrate_cells(current)
        result = service._resolve_event(
            profile,
            current,
            current.cells[cell_id],
            "on_enter_cell",
        )
        service._save_current(profile, current)
        return result


def set_current_terrain(players, player_id: str, terrain_id: str, *, edge: str | None = None) -> int:
    with players.transaction(player_id) as profile:
        current = MapInstance.model_validate(profile.current_map)
        candidates = [cell for cell in current.cells if cell.terrain_id == terrain_id and cell.passable]
        if edge == "right":
            candidates = [cell for cell in current.cells if cell.x == current.width - 1 and cell.passable]
        cell = candidates[0]
        current.current_cell_id = cell.cell_id
        cell.explored = True
        profile.current_map = current.model_dump(mode="json")
        profile.world_maps[f"{current.world_id}:{current.region_id}"] = profile.current_map
        return cell.cell_id


def test_every_world_region_is_16_by_16(content, catalog):
    players, player, service = create_service(content, catalog)
    for template_id in service.templates:
        current, _ = service.enter(player.player_id, template_id, refresh=True, seed=7)
        assert (current.width, current.height, len(current.cells)) == (16, 16, 256)
        expected = service.templates[template_id].terrain_counts
        actual = {
            terrain_id: sum(cell.terrain_id == terrain_id for cell in current.cells)
            for terrain_id in expected
        }
        assert actual == expected


def test_map_generation_is_reproducible(content, catalog):
    players = InMemoryPlayerRepository()
    first = PlayerService(players, catalog, content).create("One", "1")
    second = PlayerService(players, catalog, content).create("Two", "1")
    service = ExplorationService(players, catalog, content)
    map_one, _ = service.enter(first.player_id, seed=17)
    map_two, _ = service.enter(second.player_id, seed=17)
    assert [cell.terrain_id for cell in map_one.cells] == [cell.terrain_id for cell in map_two.cells]


def test_plain_is_not_gatherable_but_resource_tile_is(content, catalog):
    players, player, service = create_service(content, catalog)
    service.enter(player.player_id, seed=5)
    plain_id = set_current_terrain(players, player.player_id, "4")
    with pytest.raises(ValueError, match="没有可采集"):
        service.gather(player.player_id, plain_id)
    forest_id = set_current_terrain(players, player.player_id, "1")
    loot, current, _ = service.gather(player.player_id, forest_id)
    assert current.cells[forest_id].gathered is True
    assert all(isinstance(quantity, int) for quantity in loot.values())


def test_gathering_guarantees_a_base_drop_when_all_chance_rolls_miss(content, catalog):
    players, player, service = create_service(content, catalog)
    service.enter(player.player_id, seed=23)
    forest_id = set_current_terrain(players, player.player_id, "1")
    service.terrains["1"]["gather_rules"] = [
        {"resource_id": "mat_1", "chance": 0.0, "minimum": 2, "maximum": 2}
    ]
    loot, _, _ = service.gather(player.player_id, forest_id)
    assert loot == {"mat_1": 2}


def test_crossing_region_edge_enters_neighbor_from_opposite_side(content, catalog):
    players, player, service = create_service(content, catalog)
    service.enter(player.player_id, "mistwood_small", seed=11)
    set_current_terrain(players, player.player_id, "4", edge="right")
    destination, _, transition = service.move_direction(player.player_id, "right")
    assert transition is not None
    assert transition.from_region_id == "mistwood"
    assert transition.to_region_id == "emerald_coast"
    assert destination.world_x == 2
    assert destination.cells[destination.current_cell_id].x == 0


def test_camp_restores_stamina_once_per_game_day(content, catalog):
    now = datetime(2026, 7, 17, 0, 0, tzinfo=timezone.utc)
    players, player, service = create_service(content, catalog, now=now)
    service.enter(player.player_id, seed=9)
    set_current_terrain(players, player.player_id, "4")
    with players.transaction(player.player_id) as profile:
        profile.stamina = 20
    rested = service.camp(player.player_id)
    assert rested.stamina == 80
    with pytest.raises(ValueError, match="已经充分休息"):
        service.camp(player.player_id)


def test_world_clock_starts_at_year_zero_and_matches_time_conditions(content, catalog):
    definition = content.document("maps/world.json")["time"]
    epoch = datetime.fromisoformat(definition["epoch_utc"])
    clock = WorldClock(definition, lambda: epoch)
    snapshot = clock.snapshot()
    assert (snapshot.year, snapshot.month, snapshot.day, snapshot.hour) == (0, 1, 1, 0)
    assert snapshot.season == "spring"
    assert snapshot.period == "night"
    assert clock.matches({"periods": ["night"]}, snapshot)
    assert not clock.matches({"periods": ["day"]}, snapshot)


def test_gather_rules_can_be_limited_to_specific_regions(content, catalog):
    players, player, service = create_service(content, catalog)
    current, _ = service.enter(player.player_id, "mistwood_small", seed=19)
    now = service.time_snapshot()
    assert service._gather_rule_matches(
        {"conditions": {"regions": ["mistwood"]}}, current, now
    )
    assert not service._gather_rule_matches(
        {"conditions": {"regions": ["ashlands"]}}, current, now
    )


def test_persistent_bear_stays_in_cave_then_expires_in_spring(content, catalog):
    winter_hour = (9 * 30 * 24) + 12
    players, player, service, now = create_controlled_service(
        content, catalog, game_hour=winter_hour
    )
    service.enter(player.player_id, "mistwood_small", seed=31)
    cave_id = set_current_terrain(players, player.player_id, "6")
    bear = next(rule for rule in service.event_rules if rule["event_id"] == "event.cave.hibernating_bear")
    bear["chance"] = 1.0

    triggered = resolve_cell_event(service, players, player.player_id, cave_id)
    assert triggered is not None and triggered.state == "triggered"
    assert triggered.trigger_count == 1

    _, action = service.event_action(
        player.player_id,
        "event.cave.hibernating_bear",
        "flee_quietly",
    )
    assert action.state == "action"
    active = resolve_cell_event(service, players, player.player_id, cave_id)
    assert active is not None and active.state == "active"
    assert active.trigger_count == 1

    definition = content.document("maps/world.json")["time"]
    now["value"] += timedelta(
        seconds=(3 * 30 * 24) * float(definition["real_seconds_per_game_hour"])
    )
    expired = resolve_cell_event(service, players, player.player_id, cave_id)
    assert expired is not None and expired.state == "expired"
    assert "外出觅食" in expired.description

    profile = players.get(player.player_id)
    assert [entry.phase for entry in profile.world_event_log] == [
        "triggered",
        "action",
        "expired",
    ]
    assert not next(iter(profile.world_event_states.values())).active


def test_event_checks_only_once_per_cell_per_game_day(content, catalog):
    players, player, service, now = create_controlled_service(
        content, catalog, game_hour=12
    )
    service.enter(player.player_id, "mistwood_small", seed=37)
    plains_id = set_current_terrain(players, player.player_id, "4")
    bells = next(rule for rule in service.event_rules if rule["event_id"] == "event.plains.distant_bells")
    bells["chance"] = 0.0
    assert resolve_cell_event(service, players, player.player_id, plains_id) is None

    bells["chance"] = 1.0
    assert resolve_cell_event(service, players, player.player_id, plains_id) is None

    definition = content.document("maps/world.json")["time"]
    now["value"] += timedelta(
        seconds=24 * float(definition["real_seconds_per_game_hour"])
    )
    result = resolve_cell_event(service, players, player.player_id, plains_id)
    assert result is not None and result.event_id == bells["event_id"]


def test_world_scoped_story_event_only_triggers_once(content, catalog):
    players, player, service, now = create_controlled_service(content, catalog)
    current, _ = service.enter(player.player_id, "mistwood_small", seed=41)
    ruins = [cell.cell_id for cell in current.cells if cell.terrain_id == "12"]
    whisper = next(rule for rule in service.event_rules if rule["event_id"] == "event.ruin.night_whisper")
    whisper["chance"] = 1.0
    for rule in service.event_rules:
        if rule is not whisper and "12" in rule.get("terrain_ids", []):
            rule["chance"] = 0.0

    first = resolve_cell_event(service, players, player.player_id, ruins[0])
    assert first is not None and first.event_id == whisper["event_id"]
    assert first.trigger_count == 1
    definition = content.document("maps/world.json")["time"]
    now["value"] += timedelta(
        seconds=24 * float(definition["real_seconds_per_game_hour"])
    )
    assert resolve_cell_event(service, players, player.player_id, ruins[1]) is None


def test_repeatable_wolf_event_can_trigger_again_on_the_next_day(content, catalog):
    players, player, service, now = create_controlled_service(content, catalog)
    service.enter(player.player_id, "mistwood_small", seed=43)
    forest_id = set_current_terrain(players, player.player_id, "1")
    wolves = next(rule for rule in service.event_rules if rule["event_id"] == "event.wild.wolf_pack")
    wolves["chance"] = 1.0

    first = resolve_cell_event(service, players, player.player_id, forest_id)
    assert first is not None and first.trigger_count == 1
    active = resolve_cell_event(service, players, player.player_id, forest_id)
    assert active is not None and active.state == "active"
    assert active.trigger_count == 1
    service.event_action(player.player_id, wolves["event_id"], "fight")

    definition = content.document("maps/world.json")["time"]
    now["value"] += timedelta(
        seconds=24 * float(definition["real_seconds_per_game_hour"])
    )
    second = resolve_cell_event(service, players, player.player_id, forest_id)
    assert second is not None and second.trigger_count == 2
    assert len(players.get(player.player_id).world_event_log) == 3


def test_blocking_event_prevents_movement_until_resolved(content, catalog):
    players, player, service, _ = create_controlled_service(content, catalog)
    current, _ = service.enter(player.player_id, "mistwood_small", seed=47)
    forest = next(cell for cell in current.cells if cell.terrain_id == "1" and cell.passable)
    with players.transaction(player.player_id) as profile:
        current = MapInstance.model_validate(profile.current_map)
        current.current_cell_id = forest.cell_id
        profile.current_map = current.model_dump(mode="json")
    wolves = next(rule for rule in service.event_rules if rule["event_id"] == "event.wild.wolf_pack")
    wolves["chance"] = 1.0
    assert resolve_cell_event(service, players, player.player_id, forest.cell_id) is not None
    target = next(
        cell for cell in current.cells
        if cell.passable and abs(cell.x - forest.x) + abs(cell.y - forest.y) == 1
    )
    with pytest.raises(ValueError, match="事件"):
        service.move(player.player_id, target.cell_id)
    service.event_action(player.player_id, wolves["event_id"], "fight")
    moved, _ = service.move(player.player_id, target.cell_id)
    assert moved.current_cell_id == target.cell_id


def test_world_event_catalog_has_configured_participants_and_interactions(content, catalog):
    _, _, service = create_service(content, catalog)
    assert len(service.event_rules) >= 20
    assert any(rule.get("actor", {}).get("type") == "npc" for rule in service.event_rules)
    assert any(rule.get("actor", {}).get("type") == "monster" for rule in service.event_rules)
    kinds = {
        action.get("kind", "narrative")
        for rule in service.event_rules
        for action in rule.get("actions", [])
    }
    assert {"narrative", "open_npc", "start_quest", "npc_combat", "monster_combat"} <= kinds
