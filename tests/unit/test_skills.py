from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from llm_rpg_server.bootstrap import build_container
from llm_rpg_server.exploration.models import WorldEventResult


def create_player(container, name="Skill Tester", race_id="1"):
    profile = container.player_service.create(name, race_id)
    return container.skills.sync_unlocks(profile.player_id)


def test_starter_skill_is_learned_and_equipped():
    container = build_container()
    profile = create_player(container)

    assert "skill.power_strike" in profile.learned_skills
    assert profile.equipped_skill_ids == ["skill.power_strike"]


def test_every_skill_uses_mp_as_its_only_cast_resource():
    container = build_container()

    for skill in container.skill_catalog.public_view().values():
        assert set(skill["costs"]) <= {"mp"}
        assert skill["costs"].get("mp", 0) > 0


def test_skill_book_is_consumed_once_and_duplicate_learning_is_safe():
    container = build_container()
    profile = create_player(container)
    with container.players.transaction(profile.player_id) as editable:
        editable.level = 2
        editable.attributes.wisdom = 6
        editable.inventory.items["11"] = 2

    learned, skill_id = container.skills.learn_from_book(profile.player_id, "11")

    assert skill_id == "skill.fireball"
    assert learned.inventory.items["11"] == 1
    assert skill_id in learned.learned_skills
    with pytest.raises(ValueError, match="已经习得"):
        container.skills.learn_from_book(profile.player_id, "11")
    assert container.players.get(profile.player_id).inventory.items["11"] == 1


def test_combat_loadout_has_five_slot_limit_and_rejects_unlearned_skill():
    container = build_container()
    profile = create_player(container)

    with pytest.raises(ValueError, match="尚未习得"):
        container.skills.equip(profile.player_id, ["skill.fireball"])
    with pytest.raises(ValueError, match="最多携带"):
        container.skills.equip(profile.player_id, [
            "skill.power_strike", "a", "b", "c", "d", "e",
        ])


def test_flight_state_uses_real_seconds_and_expires(monkeypatch):
    container = build_container()
    profile = create_player(container)
    started_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    with container.players.transaction(profile.player_id) as editable:
        editable.level = 4
        editable.attributes.wisdom = 8
        editable.current_mp = 100
        container.skills._grant(editable, "skill.flight", "admin", "test")
    monkeypatch.setattr(container.skills, "now", lambda: started_at)

    active, outcome = container.skills.cast_exploration(profile.player_id, "skill.flight")

    assert outcome["applied_states"] == ["flying"]
    assert active.exploration_effects[0].expires_at == started_at + timedelta(seconds=90)
    monkeypatch.setattr(container.skills, "now", lambda: started_at + timedelta(seconds=91))
    assert container.skills.has_active_state(profile.player_id, ["flying"]) is False


def test_event_skill_and_timed_state_produce_distinct_event_options(monkeypatch):
    container = build_container()
    profile = create_player(container)
    started_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    monkeypatch.setattr(container.skills, "now", lambda: started_at)
    with container.players.transaction(profile.player_id) as editable:
        editable.level = 4
        editable.attributes.agility = 6
        editable.attributes.wisdom = 8
        editable.current_mp = 100
        editable.stamina = 100
        container.skills._grant(editable, "skill.lockpicking", "admin", "test")
        container.skills._grant(editable, "skill.flight", "admin", "test")

    options = container.world_events.action_options(
        profile.player_id, "event.ruin.locked_courtyard"
    )
    assert options["unlock_with_skill"]["eligible_skills"][0]["skill"]["id"] == "skill.lockpicking"
    assert options["fly_over_gate"]["visible"] is False

    container.skills.cast_exploration(profile.player_id, "skill.flight")
    options = container.world_events.action_options(
        profile.player_id, "event.ruin.locked_courtyard"
    )
    assert options["fly_over_gate"]["visible"] is True


def test_world_event_result_accepts_skill_actions_and_options():
    event = WorldEventResult.model_validate({
        "event_id": "event.locked_courtyard",
        "kind": "discovery",
        "title": "上锁的院门",
        "description": "院门紧锁。",
        "trigger": "on_enter_cell",
        "actions": [{
            "action_id": "unlock_with_skill",
            "label": "使用开锁术",
            "kind": "use_skill",
            "eligible_skills": [{"skill_id": "skill.lockpicking"}],
        }],
    })

    action = event.model_dump(mode="json")["actions"][0]
    assert action["kind"] == "use_skill"
    assert action["eligible_skills"] == [{"skill_id": "skill.lockpicking"}]
