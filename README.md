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
├── players/        Player profiles, race growth, quests, economy, and repositories
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

- `configs/catalog/`: races, internal combat archetypes, equipment, items, resources, and game rules
- `configs/progression/`: level curve, attribute points, base resources, and experience rewards
- `configs/combat/`: attributes, damage, statuses, and environment hazards
- `configs/prompts/`: versioned LLM prompts
- `configs/narratives/`: localized player-facing text
- `configs/npcs/`: NPC profiles and interaction rules
- `configs/maps/`: worlds, regions, map sizes, terrain, templates, and encounter rules
- `configs/monsters/`: lightweight enemy combat and drop profiles

Run `python scripts/validate_content.py` after changing content. The provider boundary can later be backed by Langfuse or another content API without changing domain services.

## Crafting workflow and rules

Crafting validates ownership, material eligibility, equipment retention, and recursive ingredient ancestry before the LangGraph workflow begins. The graph has a content-only concept node (coherent transformation, name, description, category), then runs artwork fusion and category-specific property generation in parallel before assembling and atomically committing the result. A concept marked incompatible ends the graph without consuming inventory and retains the reason in the failed-recipe catalogue.

`configs/crafting/categories.json` is the category registry. It maps each allowed category to an inventory type, its versioned property prompt, fixed use contexts, and numeric/enum constraints. The model proposes only a structured property candidate; the server discards undeclared fields and clamps every declared value before it can affect combat, recovery, or inventory behavior.

Artwork uses the separate Flux image task service. It first uploads a source icon to OSS when a catalogue item only has a local `/assets/...` reference, submits the two OSS keys to `/api/v1/task/edit`, polls the task status, and persists only the resulting `image_key` on the crafted item. If generation or polling fails, the server creates a diagonal split composite from the two source images and uploads that to the same bucket instead.

Configure the production service with these environment variables:

```text
CRAFT_IMAGE_SERVICE_URL=http://image-service:60668
CRAFT_OSS_BASE_URL=https://oss.toolup.cn
CRAFT_OSS_BUCKET=your-game-assets-bucket
CRAFT_WEB_ASSETS_ROOT=/path/to/llm-rpg-web/public
CRAFT_IMAGE_TIMEOUT_SECONDS=120
CRAFT_IMAGE_POLL_INTERVAL_SECONDS=2
```

The web client needs matching build-time values `VITE_RPG_OSS_BASE` and `VITE_RPG_OSS_BUCKET`. It resolves `image_key` to the OSS file endpoint and stores successfully fetched images in browser Cache Storage (backed by the browser's local disk); CORS failure falls back to direct OSS image loading.

Both successful and failed unordered ingredient pairs are stored in the shared recipe repository. Failed attempts do not consume inventory and reuse the recorded reason on later attempts, avoiding another model call. `GET /api/game/recipes` returns both outcomes through `success`, nullable `result`, and `failure_reason`. The current repository is process-local, matching the existing in-memory player and catalog lifecycle.

Generated materials and consumables use a stable ID derived from the unordered recipe key, so repeated crafting increases one inventory quantity. Generated weapons and armor keep unique instance IDs in preparation for instance state such as durability.

## Exploration and encounters

The default world is a 3 × 3 grid of nine countries/regions. Every region is a deterministic 16 × 16 map whose exact terrain counts are configured in `configs/maps/world.json`. Crossing a regional edge through `POST /api/map/move-direction` enters the adjacent country from the opposite edge; visited regional maps remain in the player world state.

Use `GET /api/map/templates` to discover regions and `GET /api/map/time` to read the global clock. `POST /api/map/enter` accepts `player_id`, optional `template_id`, `refresh`, and optional deterministic `seed`. Existing click movement remains available through `POST /api/map/move`; gathering, eating, camping, and full inn recovery use `/api/map/gather`, `/api/map/eat`, `/api/map/camp`, and `/api/map/inn`.

NPC placement is defined independently in `configs/maps/encounters.json`. An encounter can target regions, map templates, terrain, landmarks, or cell IDs and can apply relationship, memory, time, or season conditions, probability, priority, cooldown, and repeatability. Settlement terrain multiplies NPC encounter probability. Map movement returns the resolved encounter without coupling exploration to dialogue or combat implementations.

See [WORLD_SYSTEM.md](WORLD_SYSTEM.md) for time, terrain, event, stamina, and extension contracts.

See [COMBAT_SYSTEM.md](COMBAT_SYSTEM.md) for the damage formula, LLM boundary, status lifecycle, environment functions, global resources, and extension points.

See [GROWTH_SYSTEM.md](GROWTH_SYSTEM.md) for race backgrounds and passives, level/experience rules, five-attribute allocation, quest experience, and configured birth towns.
