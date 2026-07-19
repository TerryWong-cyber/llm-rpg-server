from __future__ import annotations

from llm_rpg_server.bootstrap import build_container


def test_character_creation_and_skill_unlock_are_recorded():
    container = build_container()
    profile = container.player_service.create("传记测试者", "1")
    profile = container.skills.sync_unlocks(profile.player_id)

    assert [entry.category for entry in profile.chronicle] == ["origin", "skill"]
    assert profile.chronicle[0].details["race_id"] == "1"
    assert profile.chronicle[1].details["skill_id"] == "skill.power_strike"
    assert all(entry.game_hour is not None for entry in profile.chronicle)


def test_level_and_attribute_growth_are_recorded():
    container = build_container()
    profile = container.player_service.create("成长记录者", "1")
    container.skills.sync_unlocks(profile.player_id)

    container.growth.award_once(
        profile.player_id,
        container.growth.experience_to_next(1),
        "chronicle:test-level",
    )
    container.growth.allocate(profile.player_id, {"vitality": 3, "strength": 2})

    entries = container.players.get(profile.player_id).chronicle
    growth_entries = [entry for entry in entries if entry.category == "growth"]
    assert [entry.details for entry in growth_entries] == [
        {"old_level": 1, "level": 2},
        {"allocations": {"vitality": 3, "strength": 2}},
    ]


def test_quest_lifecycle_is_recorded_once_per_transition():
    container = build_container()
    profile = container.player_service.create("任务记录者", "1")
    hook = container.npc_interactions.activate_story_hook(
        "evelyn_mossmark",
        profile.player_id,
        "evelyn_lost_spores",
        source="chronicle-test",
    )
    with container.players.transaction(profile.player_id) as editable:
        editable.inventory.materials["mat_3"] = 2

    container.growth.complete_quest(
        profile.player_id,
        "evelyn_mossmark",
        hook.hook_id,
    )

    quest_entries = [
        entry for entry in container.players.get(profile.player_id).chronicle
        if entry.category == "quest"
    ]
    assert [entry.source_id for entry in quest_entries] == [
        f"quest-start:{hook.hook_id}",
        f"quest-complete:{hook.hook_id}",
    ]
