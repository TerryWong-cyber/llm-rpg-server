# LLM RPG Server

This repository contains a modular FastAPI RPG server. Deterministic services own inventory, combat, exploration, encounters, and rewards. LLM integrations are limited to narrative generation and consume versioned local content through a provider interface.

## Structure

```text
src/llm_rpg_server/
├── api/            HTTP and WebSocket adapters
├── catalog/        Runtime catalog access
├── combat/         Deterministic combat graph and room sessions
├── crafting/       Transactional crafting and recipe discovery
├── exploration/    Scaled maps, map instances, movement, and gathering
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

Run `python scripts/validate_content.py` after changing content. The provider boundary can later be backed by Langfuse or another content API without changing domain services.

## Exploration and encounters

Map dimensions are configured in `configs/maps/world.json`. The initial presets are:

- `small`: 5 × 5
- `medium`: 10 × 10
- `large`: 20 × 20
- `custom`: explicit width and height in a map template

Use `GET /api/map/templates` to discover templates. `POST /api/map/enter` accepts `player_id`, optional `template_id`, `refresh`, and optional deterministic `seed`. Movement uses `POST /api/map/move`; gathering keeps the existing `POST /api/map/gather` route.

NPC placement is defined independently in `configs/maps/encounters.json`. An encounter can target regions, map templates, terrain, landmarks, or cell IDs and can apply relationship or memory conditions, probability, priority, cooldown, and repeatability. Map movement returns the resolved encounter without coupling the exploration domain to dialogue or combat implementations.
