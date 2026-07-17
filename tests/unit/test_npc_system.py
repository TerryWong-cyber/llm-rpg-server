def test_threat_arms_combat_and_records_bilateral_memories(npc_service):
    player_id = "test_player"
    result = npc_service.interact("darok_blacksalt", player_id, "交出货单，否则我就动手。")
    assert result["intent"] == "threat"
    assert result["combat_trigger"]["trigger_id"] == "darok_defend_cargo"
    assert result["relationship"].hostility >= 10
    assert len(npc_service.npc_memories("darok_blacksalt", player_id)) == 1
    assert len(npc_service.player_memories(player_id)) == 1
    npc, combat = npc_service.start_combat("darok_blacksalt", player_id, "darok_defend_cargo")
    assert npc.name == "达洛克·黑盐"
    assert combat.character_id == "2"
    npc_service.record_combat_outcome("darok_blacksalt", player_id, player_won=True)
    view = npc_service.get_npc_view("darok_blacksalt", player_id)
    assert view["relationship"].respect >= 8
    assert any("combat_victory" in memory.tags for memory in npc_service.player_memories(player_id))
    assert any("combat_outcome" in fact.tags for fact in npc_service.world_facts())


def test_players_have_isolated_npc_relationships(npc_service):
    first, second = "first_player", "second_player"
    before = npc_service.get_npc_view("darok_blacksalt", second)["relationship"].hostility
    npc_service.interact("darok_blacksalt", first, "交出货单，否则我就动手。")
    assert npc_service.get_npc_view("darok_blacksalt", first)["relationship"].hostility > before
    assert npc_service.get_npc_view("darok_blacksalt", second)["relationship"].hostility == before
    assert npc_service.npc_memories("darok_blacksalt", second) == []


def test_public_npc_view_does_not_expose_private_backstory(npc_service):
    view = npc_service.list_npcs(terrain_id="5")[0]
    serialized = str(view)
    assert "private_secret" not in serialized
    assert "难民姓名" not in serialized


def test_event_npcs_publish_portrait_equipment_and_story_hooks(npc_service):
    view = npc_service.get_npc_view("seren_gravewarden", "test_player")["npc"]
    assert view["image_url"].endswith("vampire-dracula.svg")
    assert view["equipment"]["weapon_id"]
    assert view["story_hooks"][0]["hook_id"] == "seren_stolen_nameplates"
    assert view["combat_tactics"]
