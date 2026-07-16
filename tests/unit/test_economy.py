import pytest

from llm_rpg_server.players import EconomyService, InMemoryPlayerRepository, PlayerService


def test_buy_and_sell_are_atomic(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Trader", "1")
    economy = EconomyService(players, catalog, content)
    economy.buy(player.player_id, "item", "2")
    assert players.get(player.player_id).inventory.items["2"] == 1
    economy.sell(player.player_id, "item", "2")
    assert players.get(player.player_id).inventory.items["2"] == 0


def test_failed_purchase_does_not_change_gold(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Trader", "1")
    before = player.gold
    economy = EconomyService(players, catalog, content)
    with pytest.raises(ValueError):
        economy.buy(player.player_id, "weapon", "missing")
    assert players.get(player.player_id).gold == before
