# LLM RPG Server

This repository contains a modular FastAPI RPG server. Deterministic services own inventory, combat, exploration, encounters, and rewards. LLM integrations are limited to narrative generation and consume versioned local content through a provider interface.

## Structure

```text
src/llm_rpg_server/
├── api/            HTTP and WebSocket adapters
├── catalog/        Runtime catalog access
├── combat/         Deterministic combat graph and room sessions
├── crafting/       Transactional crafting and recipe discovery
├── exploration/    Regional maps, movement, stamina, gathering, and world travel
├── monsters/       Lightweight configured enemies, equipment, tactics, and drops
├── npcs/           NPC profiles, dialogue, relationships, and memory
├── players/        Player profiles, inventory, economy, and repositories
├── shared/         Content, settings, LLM, and observability adapters
└── world/          Cross-domain encounter orchestration
```

Static content lives under `configs/`. Python modules reference stable content IDs rather than embedding prompts, NPC definitions, or player-facing narrative.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
uvicorn llm_rpg_server.main:app --reload
```

The legacy command `uvicorn server:app_api --reload` remains available through the thin compatibility entry point.

## Content

- `configs/catalog/`: characters, equipment, items, resources, and game rules
- `configs/prompts/`: versioned LLM prompts
- `configs/narratives/`: localized player-facing text
- `configs/npcs/`: NPC profiles and interaction rules
- `configs/maps/`: worlds, regions, map sizes, terrain, templates, and encounter rules
- `configs/monsters/`: lightweight enemy combat and drop profiles

Run `python scripts/validate_content.py` after changing content. The provider boundary can later be backed by Langfuse or another content API without changing domain services.

## Crafting rules

Crafting uses a deterministic rule layer around a structured LLM decision. The server validates ownership, material eligibility, equipment retention, and recursive ingredient ancestry before asking the model to judge whether the combination has a coherent transformation path. Successful results expose `can_be_ingredient`; generated items also retain internal ancestry metadata so they cannot be combined with any direct or transitive ingredient.

Both successful and failed unordered ingredient pairs are stored in the shared recipe repository. Failed attempts do not consume inventory and reuse the recorded reason on later attempts, avoiding another model call. `GET /api/game/recipes` returns both outcomes through `success`, nullable `result`, and `failure_reason`. The current repository is process-local, matching the existing in-memory player and catalog lifecycle.

Generated materials and consumables use a stable ID derived from the unordered recipe key, so repeated crafting increases one inventory quantity. Generated weapons and armor keep unique instance IDs in preparation for instance state such as durability.

## Exploration and encounters

The default world is a 3 × 3 grid of nine countries/regions. Every region is a deterministic 16 × 16 map whose exact terrain counts are configured in `configs/maps/world.json`. Crossing a regional edge through `POST /api/map/move-direction` enters the adjacent country from the opposite edge; visited regional maps remain in the player world state.

Use `GET /api/map/templates` to discover regions and `GET /api/map/time` to read the global clock. `POST /api/map/enter` accepts `player_id`, optional `template_id`, `refresh`, and optional deterministic `seed`. Existing click movement remains available through `POST /api/map/move`; gathering, eating, and camping use `/api/map/gather`, `/api/map/eat`, and `/api/map/camp`.

NPC placement is defined independently in `configs/maps/encounters.json`. An encounter can target regions, map templates, terrain, landmarks, or cell IDs and can apply relationship, memory, time, or season conditions, probability, priority, cooldown, and repeatability. Settlement terrain multiplies NPC encounter probability. Map movement returns the resolved encounter without coupling exploration to dialogue or combat implementations.

See [WORLD_SYSTEM.md](WORLD_SYSTEM.md) for time, terrain, event, stamina, and extension contracts.
