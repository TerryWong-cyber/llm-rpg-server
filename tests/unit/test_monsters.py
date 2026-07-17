from llm_rpg_server.monsters import MonsterCatalog


def test_monster_catalog_exposes_combat_and_drop_configuration(content, catalog):
    monsters = MonsterCatalog(content, catalog)
    assert len(monsters.list_all()) == 9
    bear = monsters.get("hibernating_bear")
    assert bear.stats.hp > 0
    assert bear.combat.threat >= 1
    assert bear.equipment.weapon_name
    assert bear.drops


def test_monster_public_view_contains_no_npc_memory_fields(content, catalog):
    view = MonsterCatalog(content, catalog).public_view("grave_wraith")
    assert view["name"] == "墓园游魂"
    assert "backstory" not in view
    assert "memories" not in view
