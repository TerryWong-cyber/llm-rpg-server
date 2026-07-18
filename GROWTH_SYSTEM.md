# Race and Growth System

The player no longer chooses a profession at character creation. `character_id` remains in the profile only as a legacy/internal combat-template field for configured NPCs. Player-facing creation accepts a race and nation through `race_id`.

## Authoritative data

- `configs/catalog/races.json` defines race background, nation, small starting-attribute differences, strengths, weaknesses, traits, exclusive skills, deterministic bonuses/passives, and birthplace.
- `configs/progression/rules.json` defines the experience curve, five points per level, base resources, and battle experience rewards.
- `players/growth.py` owns experience, level-up, allocation, quest objectives, item consumption, and idempotent rewards.
- `players/service.py` converts a player profile into the combat character used by the combat rulebook.

The LLM may describe how a racial trait appears in the narrative. It cannot grant experience, levels, points, quest completion, racial bonuses, or skills.

## Level curve and attributes

All players begin at level 1 with zero experience and zero unspent points. Experience required for the next level is:

```text
round(100 * current_level ^ 1.35)
```

Every level grants five unspent points. The allocatable attributes are:

- vitality: maximum HP and status resistance;
- strength: physical power, HP, stamina, and physical resistance;
- agility: accuracy, evasion, critical chance, and stamina;
- wisdom: spell power, MP, magic resistance, and status resistance;
- luck: accuracy, critical chance, and status resistance.

Current base resource formulas, before equipment, are:

```text
max_hp      = 38 + strength * 2 + vitality * 7
max_mp      = 18 + wisdom * 2
max_stamina = 80 + strength + agility * 2
```

Allocating points increases maxima and restores only the newly gained resource capacity. It does not refill already missing HP, MP, or stamina.

## Races

The initial catalogue contains two human national origins plus moon elves, mountain dwarves, frost barbarians, and vampires. Attribute totals differ by at most one point at level 1. Larger identity differences come from deterministic traits:

- night vision modifies accuracy/evasion in night or dark contexts;
- vampire sunlight penalties activate only when the world-time context includes `sunlight`;
- vampire life steal converts a configured share of dealt damage into HP;
- elemental/status/resistance bonuses are merged into derived combat stats;
- each race exposes one stable `r:*` exclusive combat action.

Race combat configuration uses the same damage, hit, resistance, status, and resource rules as weapon skills. The LLM effectiveness judge receives qualitative traits and environment tags but never changes the configured numeric passive.

## Birthplaces

Every race declares `world_id`, `region_id`, `template_id`, `cell_id`, and settlement name. On first map entry, an omitted `template_id` resolves to this birthplace. Configuration validation requires the cell to be a village/town with both `shop` and `inn` interactions.

The current birthplaces are:

- 瓦伦王国人族: 雾杉林 · 晨曦镇;
- 萨赫赤砂国人族: 赤砂商道 · 盐冠城;
- 月桂精灵: 月影谷 · 银月城;
- 断脊矮人: 断脊山道 · 铜炉镇;
- 霜原蛮族: 霜冠冻土 · 星骨营地;
- 暮裔吸血鬼: 月影谷 · 暮影镇.

## Experience sources

- Random PvE, configured NPC combat, monster combat, and PvP victories grant configured experience.
- Combat reward operations use the existing repository idempotency keys so a room cannot pay twice.
- Story hooks include explicit deterministic requirements such as inventory quantities or reaching a region.
- Completing a quest validates every requirement, optionally consumes submitted items, grants experience once, and moves the hook from active to completed state.

## HTTP contracts

```text
POST /api/game/character/create
{ "name": "...", "race_id": "1" }

POST /api/game/character/allocate
{ "player_id": "...", "allocations": { "vitality": 2, "wisdom": 3 } }

POST /api/game/quest/complete
{ "player_id": "...", "npc_id": "...", "hook_id": "..." }
```

`GET /api/game/meta` now exposes `races`. Player profiles and combat/exploration snapshots expose level, experience, the next threshold, points, attributes, and quest state.

## Persistence limitation

Growth is global across exploration and battles for the lifetime of the player profile. The current repository is still process-local memory, so server restart clears profiles, progression, quests, maps, and relationships until a persistent repository is introduced.
