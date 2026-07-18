from __future__ import annotations

from llm_rpg_server.bootstrap import build_container


def test_combat_uses_and_persists_global_resources():
    container = build_container()
    profile = container.player_service.create("资源测试者", "1")
    with container.players.transaction(profile.player_id) as editable:
        editable.current_hp //= 2
        editable.current_mp //= 2
        editable.stamina //= 2
    before = container.players.get(profile.player_id)

    container.combat.engine.llm = None
    container.combat.engine.effectiveness.llm = None
    room = container.combat.create_room(profile.player_id)
    container.combat.add_ai(room.room_id)
    snapshot = container.combat.snapshot(room)

    assert snapshot is not None
    assert snapshot["state"]["player_hp"] == before.current_hp
    assert snapshot["state"]["player_mp"] == before.current_mp
    assert snapshot["state"]["player_stamina"] == before.stamina

    next_snapshot = container.combat.submit_action(room, profile.player_id, "0")
    persisted = container.players.get(profile.player_id)

    assert next_snapshot is not None
    assert persisted.current_hp == next_snapshot["state"]["player_hp"]
    assert persisted.current_mp == next_snapshot["state"]["player_mp"]
    assert persisted.stamina == next_snapshot["state"]["player_stamina"]

    next_room = container.combat.create_room(profile.player_id)
    container.combat.add_ai(next_room.room_id)
    next_prep = container.combat.snapshot(next_room)

    assert next_prep is not None
    assert next_prep["state"]["player_hp"] == persisted.current_hp
    assert next_prep["state"]["player_mp"] == persisted.current_mp
    assert next_prep["state"]["player_stamina"] == persisted.stamina


def test_defeat_keeps_one_hp_and_persists_weakness():
    container = build_container()
    profile = container.player_service.create("败北测试者", "2")
    values = {
        "player_hp": 0,
        "player_mp": 3,
        "player_stamina": 4,
        "player_statuses": [],
        "player_stats": {
            "max_hp": profile.max_hp,
            "max_mp": profile.max_mp,
            "max_stamina": profile.max_stamina,
        },
    }

    container.combat._persist_side(profile.player_id, "player", values, game_over=True)
    persisted = container.players.get(profile.player_id)

    assert persisted.current_hp == 1
    assert persisted.current_mp == 3
    assert persisted.stamina == 4
    assert [status.status_id for status in persisted.combat_statuses] == ["weakness"]
