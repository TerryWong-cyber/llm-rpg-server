from __future__ import annotations

import random

from llm_rpg_server.combat.adjudication import EffectivenessJudge
from llm_rpg_server.combat.hazards import EnvironmentHazardService
from llm_rpg_server.combat.models import DamagePacket, DerivedStats
from llm_rpg_server.combat.rules import CombatRulebook


def test_damage_packet_keeps_physical_magic_elemental_and_true_channels(content, catalog):
    rules = CombatRulebook(content)
    attacker = rules.derive_stats(catalog.characters["3"], catalog.weapons["3"], catalog.armors["1"])
    unarmored = rules.derive_stats(catalog.characters["1"], catalog.weapons["1"], catalog.armors["1"])
    protected = rules.derive_stats(catalog.characters["1"], catalog.weapons["1"], catalog.armors["5"])
    packet = DamagePacket(physical=30, magical=30, elemental={"fire": 30}, true=30)

    baseline = rules.resolve_damage(
        packet, attacker, unarmored, 5, random.Random(7), can_miss=False, can_crit=False
    )
    mitigated = rules.resolve_damage(
        packet, attacker, protected, 5, random.Random(7), can_miss=False, can_crit=False
    )

    assert mitigated.physical < baseline.physical
    assert mitigated.magical < baseline.magical
    assert mitigated.elemental["fire"] < baseline.elemental["fire"]
    assert mitigated.true == baseline.true == 30


def test_effectiveness_score_is_a_bounded_damage_coefficient(content, catalog):
    rules = CombatRulebook(content)
    attacker = rules.derive_stats(catalog.characters["1"], catalog.weapons["1"], catalog.armors["1"])
    target = DerivedStats()
    packet = DamagePacket(physical=20)

    ineffective = rules.resolve_damage(
        packet, attacker, target, 0, random.Random(1), can_miss=False, can_crit=False
    )
    normal = rules.resolve_damage(
        packet, attacker, target, 5, random.Random(1), can_miss=False, can_crit=False
    )
    decisive = rules.resolve_damage(
        packet, attacker, target, 10, random.Random(1), can_miss=False, can_crit=False
    )

    assert ineffective.total == 0
    assert normal.total > 0
    assert decisive.total == normal.total * 2


def test_statuses_stack_tick_and_respect_duration(content):
    rules = CombatRulebook(content)
    target = DerivedStats(status_resistance=0)
    statuses = rules.apply_status([], "poison", "attacker", target, random.Random(1), base_chance=1)
    statuses = rules.apply_status(statuses, "poison", "attacker", target, random.Random(1), base_chance=1)

    assert statuses[0]["stacks"] == 2
    remaining, damage = rules.tick_statuses(statuses, target, random.Random(2))
    assert damage.elemental["poison"] == 8
    assert remaining[0]["remaining_turns"] == 3


def test_environment_fixed_functions_are_monotonic_and_bounded(content):
    rules = CombatRulebook(content)

    assert rules.fall_damage(20, 80, 1.0) > rules.fall_damage(5, 80, 1.0)
    assert rules.fall_damage(10, 80, 1.2) > rules.fall_damage(10, 80, 0.4)
    assert rules.drowning_damage(4, 100) > rules.drowning_damage(1, 100)
    assert rules.drowning_damage(100, 100) <= 35


def test_fall_estimator_preserves_known_inputs_and_delegates_to_fixed_rule(content):
    rules = CombatRulebook(content)
    hazards = EnvironmentHazardService(rules, content, llm=None)

    result = hazards.resolve_fall(
        "骑士从石墙跌落到泥地",
        height_m=8,
        mass_kg=95,
        ground_hardness=0.3,
        mitigation=0.2,
    )

    assert result.estimate.height_m == 8
    assert result.damage == rules.fall_damage(8, 95, 0.3, 0.2)


def test_pvp_effectiveness_is_deterministic_and_llm_free(content):
    judge = EffectivenessJudge(content, llm=object())
    state = {
        "game_mode": "PvP",
        "player_action": {"type": "attack"},
        "ai_action": {"type": "defense"},
        "player_statuses": [],
        "ai_statuses": [],
    }

    result = judge.evaluate(state, {})

    assert result.player.score == 2
    assert result.opponent.score == 5
