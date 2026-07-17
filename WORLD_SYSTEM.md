# World and Map System

## Topology and persistence

The configured world is a 3 × 3 grid. Each grid position owns exactly one country/region and one 16 × 16 `MapInstance`. The player's `world_seed` plus the stable world and region IDs derive each regional seed, so generation is reproducible across Python processes. `world_maps` stores every visited region while `current_map` remains the compatibility view used by existing clients.

Crossing a local edge with `move-direction` looks up the neighboring world coordinate and places the player on the closest passable cell on the opposite edge. A move past the outer 3 × 3 boundary is rejected.

The current repository is process-local. The model is ready for a database-backed `PlayerRepository`, but remote deployments must add that adapter before world state can survive process replacement.

## Global time

`configs/maps/world.json` defines a fixed `epoch_utc` and `real_seconds_per_game_hour`. The default is 10 real seconds per game hour, with 24 hours per day, 30 days per month, 12 months per year, and four three-month seasons. A fixed epoch makes time global and restart-stable without saving a mutable timer.

Changing `epoch_utc` resets the world's calendar origin. Do not change it after release unless a deliberate world-time reset is intended.

Time conditions accept:

- `periods`: `dawn`, `day`, `dusk`, `night`
- `seasons`: `spring`, `summer`, `autumn`, `winter`
- `months`
- `hour_start` and `hour_end`, including ranges that cross midnight

Gather rules may additionally declare `regions`. This is evaluated by the exploration service rather than the clock and lets one terrain produce regional specialties without duplicating the terrain definition.

## Terrain contract

Terrain definitions own deterministic gameplay facts:

- `category` and `tags` describe ordinary, resource, settlement, interactive, or blocked cells.
- `passable`, `movement_cost`, and `campable` control travel.
- `gatherable` and `gather_rules` control drops and their time, season, and region conditions.
- `image_url` is presentation metadata for the frontend; terrain behavior never depends on an asset being available.
- `interactions` controls access such as shop, NPC, inn, quest, or investigation.
- `npc_chance_multiplier` raises settlement encounter density without changing NPC rules.

Plain terrain is intentionally non-gatherable. Villages and towns allow trading only during configured opening periods. Event rules remain separate from terrain and can match region IDs, terrain IDs, terrain tags, periods, and seasons.

The starter catalog currently contains 40 resources across wood, plants, minerals, creature parts, aquatic materials, arcane materials, regional specialties, and relics. Every gatherable terrain has a dependable base drop plus lower-probability secondary or rare drops, so gathering does not consume a cell only to return an empty bag under normal configuration.

## Stamina

Movement charges the destination terrain's cost. Gathering and combat use global action costs. Food declares `stamina_restore` in the catalog. Camping restores a configured amount, is limited to campable terrain, and can occur once per game day. The global clock never jumps when a player rests.

All costs and access checks are server-authoritative. The frontend's disabled states are previews only.

## Stateful world events

World events are configuration-driven, but their checks, trigger counts, active location, actions, and history are server-authoritative. Each event may declare:

- `trigger_scope`: `cell`, `region`, or `world`; this decides which locations share one trigger counter and cooldown.
- `cooldown_days`: minimum game days between probability checks. The default and minimum are one, so repeatedly entering the same cell on the same day never rerolls it.
- `max_triggers`: a positive limit, or `null` for repeatable incidental events. A world-scoped event with `max_triggers: 1` is suitable for unique story discoveries.
- `persistence`: `none` for momentary events, `while_conditions_match` for condition-bound state, or `until_resolved` for an event that remains until the player chooses a resolution.
- `actor`: an optional configured `npc` or lightweight `monster`. Public participant data is resolved by the event coordinator and never copied into map state.
- `actions`: player choices with a `kind` (`narrative`, `open_npc`, `start_quest`, `npc_combat`, or `monster_combat`), target IDs, and a `keep_active` or `end_event` resolution.
- `blocks_movement`: prevents leaving the bound cell while an event is active. Forced encounters expose a single marked combat action rather than silently changing screens.

Persistent events are returned as `active` on later visits instead of being rolled again. When their conditions stop matching, the next visit returns an `expired` result and records the change. The winter bear demonstrates this lifecycle: it remains in the same cave after the player quietly leaves, then disappears when that cave is revisited outside winter. The wolf pack demonstrates an unlimited incidental event that may roll again on a later game day.

`PlayerProfile.world_event_states` stores the mechanical state. `world_event_log` is an append-only player-facing journal capped at 200 entries. Map responses include active event IDs per cell and the journal; choices are submitted through `POST /api/map/event-action`.

`WorldEventCoordinator` is the only cross-domain router. Exploration owns trigger/cooldown/persistence, NPC services own relationships/memories/tasks, the monster catalog owns combat profiles/drops, and combat owns rooms and reward settlement. Monster rewards are configured, deterministic per battle room, and idempotent.

The starter content contains 21 world events, 8 NPC profiles, and 9 monster profiles. NPCs retain full background, relationships, equipment, hooks, and combat memories. Monsters deliberately omit those expensive social fields and contain only presentation, stats, tactics, equipment, gold, and drops.

## Extension points

To add content without changing domain code:

1. Add a catalog resource or food item.
2. Add or update terrain `gather_rules`, time conditions, and exact template `terrain_counts`.
3. Ensure every region template still totals exactly 256 cells.
4. Add world events under `events`, NPCs under `npcs/demo_npcs.json`, monsters under `monsters/world_monsters.json`, or ambient NPC encounters in `maps/encounters.json`.
5. Run `pytest` on the deployment environment and `python scripts/validate_content.py` after editing content.
