# Combat System

The combat domain keeps qualitative judgement and authoritative game state separate. LLM output can explain situational advantages in PvE, but only deterministic code changes resources, applies statuses, calculates damage, grants rewards, or declares the winner.

## Module boundaries

- `combat/adjudication.py`: qualitative 0–10 effectiveness assessment. PvE may use the configured LLM; PvP always uses deterministic fallback rules.
- `combat/rules.py`: pure attribute derivation, hit/critical rolls, damage mitigation, status application/ticks, and environmental hazard functions.
- `combat/hazards.py`: bounded LLM parameter estimation that delegates final damage to the rulebook.
- `combat/engine.py`: LangGraph round orchestration and narration. It does not contain resistance curves or status definitions.
- `combat/service.py`: room coordination, loadouts, inventory consumption, and synchronization with the player repository.
- `configs/combat/rules.json`: balance parameters, status definitions, environment profiles, and aliases.
- `configs/prompts/combat_effectiveness.json`: the LLM permission boundary and structured qualitative input.

## Derived attributes

Strength contributes physical power, maximum HP, physical resistance, and a small amount of maximum stamina. Wisdom contributes spell power, maximum MP, magic resistance, and status resistance. Agility contributes hit rating, evasion, critical chance, and maximum stamina. Luck contributes critical chance, hit rating, and status resistance.

Equipment adds explicit physical/magic resistance, elemental resistance, accuracy, evasion, status resistance, and physical/magic penetration. New catalog entries may omit these fields; the rulebook retains legacy fallbacks for generated equipment.

## Round resolution

1. Resolve the qualitative effectiveness score. `0` cannot connect, `5` is a neutral `1.0×`, and `10` is a decisive `2.0×`.
2. Derive current attributes after debuff modifiers.
3. Roll deterministic hit and critical results from `combat_seed + turn + namespace`.
4. Split the attack into physical, generic magical, elemental magical, and true damage.
5. Apply effectiveness and critical multipliers. True damage receives the effectiveness coefficient but cannot critically strike.
6. Apply physical or magical penetration and the resistance curve. Elemental damage passes through magic resistance plus its element resistance. True damage bypasses both.
7. Tick existing damage-over-time effects, apply new statuses, and resolve environmental exposure.
8. Commit HP, MP, stamina, inventory, and persistent statuses to the player repository.

For non-negative resistance, the multiplier is `100 / (100 + resistance)`. Negative effective resistance amplifies damage with `2 - 100 / (100 - resistance)`. Hit chance is derived from `(accuracy - evasion) / 100` and clamped to the configured 5–95% range.

Every snapshot includes `last_resolution`, containing effectiveness reasons, hit chance, critical state, per-channel damage, status damage, and environmental damage. This is the audit record used by the frontend instead of recalculating combat locally.

## Status lifecycle

Statuses are independent instances with source, stack count, potency, remaining turns, persistence, and tags. Poison, burn, frostbite, weakness, fear, stun, and root are configured initially.

Poison, burn, frostbite, and weakness persist after combat. Fear, stun, and root are combat-local. Defeat preserves the character at 1 HP and adds persistent weakness. Antidote-style items clear statuses tagged `negative`.

## Environmental damage

Environment text is normalized into bounded tags and configured hazards. High temperature deals fire-type magical damage and may burn; toxic gas applies poison; submersion tracks air exposure and then deals escalating true damage.

`CombatRulebook.fall_damage()` accepts height, mass, surface hardness, and mitigation. `drowning_damage()` accepts turns without air and maximum HP. `EnvironmentHazardService.resolve_fall()` may ask the LLM to populate only missing, schema-bounded measurements from narration; supplied measurements are preserved and the fixed function remains authoritative.

## Global resources and recovery

HP, MP, stamina, equipped loadout, and persistent statuses live in `PlayerProfile`. Starting a battle reads the current values and never refills them. Every completed round writes them back, so disconnecting or entering another battle cannot reset resources.

Physical weapon skills spend stamina; magical weapon skills spend MP. Normal attack and tactical defense remain zero-cost fallbacks. Camping restores configured portions of HP and MP plus stamina once per game day. `POST /api/map/inn` fully restores resources and clears persistent combat statuses when the player is on settlement terrain with an `inn` interaction.

The repository is still process-local. The state is global across game features and battles, but not across server restarts; replacing `InMemoryPlayerRepository` with a persistent adapter does not require changing combat rules.
