from llm_rpg_server.exploration import ExplorationService
from llm_rpg_server.players import InMemoryPlayerRepository, PlayerService


def test_map_scale_controls_dimensions(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Explorer", "1")
    service = ExplorationService(players, catalog, content)
    small, _ = service.enter(player.player_id, "mistwood_small", seed=7)
    assert (small.width, small.height, len(small.cells)) == (5, 5, 25)
    medium, _ = service.enter(player.player_id, "desert_medium", refresh=True, seed=7)
    assert (medium.width, medium.height, len(medium.cells)) == (10, 10, 100)


def test_map_generation_is_reproducible(content, catalog):
    players = InMemoryPlayerRepository()
    first = PlayerService(players, catalog, content).create("One", "1")
    second = PlayerService(players, catalog, content).create("Two", "1")
    service = ExplorationService(players, catalog, content)
    map_one, _ = service.enter(first.player_id, seed=17)
    map_two, _ = service.enter(second.player_id, seed=17)
    assert [cell.terrain_id for cell in map_one.cells] == [cell.terrain_id for cell in map_two.cells]


def test_gathered_materials_use_uniform_quantities(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Gatherer", "1")
    service = ExplorationService(players, catalog, content)
    current, _ = service.enter(player.player_id, seed=5)
    service.gather(player.player_id, current.current_cell_id)
    assert all(isinstance(quantity, int) for quantity in players.get(player.player_id).inventory.materials.values())

