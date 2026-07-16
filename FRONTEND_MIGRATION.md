# Frontend Migration Contract

## 1. Purpose

This document is the standalone integration contract for adapting the frontend to `llm-rpg-server` 3.x. A frontend task should be able to implement the migration without scanning the backend repository.

- Contract date: 2026-07-15
- Backend API version: `3.0.0`
- Default HTTP origin: `http://<host>:8008`
- OpenAPI UI: `/docs`
- OpenAPI document: `/openapi.json`
- WebSocket origin: `ws://<host>:8008`
- Persistence: process memory only
- Authentication: not implemented yet; `player_id` is the current identity token

Production deployments using TLS must use `https://` and `wss://`.

## 2. Frontend Integration Rules

1. Treat all catalog IDs as strings, even though some catalog objects contain a numeric `id` field.
2. Keep `player_id` after character creation and send it with every player-scoped request.
3. Treat the server response as authoritative for gold, inventory, combat state, map state, NPC relationships, and rewards.
4. Do not calculate damage, rewards, gathering loot, relationship changes, or crafting results in the frontend.
5. Use `snapshot.state.player_inventory` after combat and `profile.inventory` after economy or crafting operations to replace local inventory state.
6. Handle HTTP errors through `detail` and WebSocket errors through `event: "error"` plus `msg`.
7. The backend currently has no player-profile restore endpoint. Cache the latest profile locally for page refresh, but do not assume it survives a backend restart.
8. Use one backend worker. Runtime repositories and LangGraph checkpoints are not shared across processes.

## 3. Recommended Frontend Modules

```text
src/
├── api/
│   ├── client.ts
│   ├── game.ts
│   ├── maps.ts
│   ├── npcs.ts
│   ├── rooms.ts
│   └── combatSocket.ts
├── contracts/
│   ├── common.ts
│   ├── catalog.ts
│   ├── player.ts
│   ├── exploration.ts
│   ├── npc.ts
│   └── combat.ts
├── stores/
│   ├── catalog.ts
│   ├── player.ts
│   ├── exploration.ts
│   ├── world.ts
│   └── combat.ts
└── features/
    ├── character/
    ├── inventory/
    ├── shop/
    ├── crafting/
    ├── exploration/
    ├── npc/
    └── combat/
```

Keep transport DTOs separate from view models. Do not let components call `fetch` or construct WebSocket messages directly.

## 4. Shared TypeScript Contracts

```ts
export type ItemType = "weapon" | "armor" | "item" | "material";
export type MapScale = "small" | "medium" | "large" | "custom";
export type GameMode = "WAITING" | "PvE" | "PvP";

export interface ApiError {
  detail: string | Array<{
    type: string;
    loc: Array<string | number>;
    msg: string;
    input?: unknown;
  }>;
}

export interface Inventory {
  weapons: string[];
  armors: string[];
  items: Record<string, number>;
  materials: Record<string, number>;
}

export interface PlayerProfile {
  player_id: string;
  name: string;
  character_id: string;
  gold: number;
  inventory: Inventory;
  current_map: MapInstance | null;
}
```

## 5. Catalog

### `GET /api/game/meta`

Load this once during application bootstrap. It is also the lookup source for inventory IDs and combat loadout rendering.

```ts
export interface CharacterDefinition {
  id: number;
  name: string;
  hp: number;
  mp: number;
  str: number;
  agi: number;
  int: number;
  desc: string;
}

export interface SkillDefinition {
  id: string;
  name: string;
  cost: number;
  multiplier: number;
  desc: string;
  status_effect?: string;
  self_effect?: string;
  self_effect_ratio?: number;
}

export interface WeaponDefinition {
  id: number | string;
  name: string;
  base_dmg: number;
  range: string;
  type: "phys" | "magic";
  value: number;
  desc: string;
  image_url?: string;
  skills: SkillDefinition[];
}

export interface ArmorDefinition {
  id: number | string;
  name: string;
  hp_bonus: number;
  def_rate: number;
  value: number;
  desc: string;
  image_url?: string;
}

export interface ConsumableDefinition {
  id: number | string;
  name: string;
  type: "heal_hp" | "heal_mp" | "heal_both" | "dmg";
  val: number;
  value: number;
  desc: string;
  image_url?: string;
}

export interface ResourceDefinition {
  id: string;
  name: string;
  emoji: string;
  value: number;
  desc?: string;
  image_url?: string;
}

export interface GameMetaResponse {
  characters: Record<string, CharacterDefinition>;
  weapons: Record<string, WeaponDefinition>;
  armors: Record<string, ArmorDefinition>;
  items: Record<string, ConsumableDefinition>;
  resources: Record<string, ResourceDefinition>;
}
```

Generated crafting results are registered into the server catalog at runtime. Merge a successful crafted definition into the frontend catalog cache or reload `/api/game/meta` after crafting.

## 6. Character and Player State

### `POST /api/game/character/create`

Request:

```ts
interface CreateCharacterRequest {
  name: string;          // 1..40 characters
  character_id: string;  // key from meta.characters
}
```

Response:

```ts
interface CreateCharacterResponse {
  status: "success";
  player_id: string;
  profile: PlayerProfile;
}
```

There is currently no login, authentication, player listing, or `GET profile` endpoint. Character creation establishes the browser session. Persist at least `player_id` and the latest profile in the frontend store.

## 7. Shop

### `POST /api/game/shop/buy`
### `POST /api/game/shop/sell`

Request:

```ts
interface TradeRequest {
  player_id: string;
  thread_id?: string | null;
  item_type: ItemType;
  item_id: string;
}
```

Normal response:

```ts
interface TradeResponse {
  status: "success";
  profile: PlayerProfile;
}
```

If `thread_id` identifies an active combat room, the response is a complete `CombatSnapshot` instead. During combat, send the snapshot's `thread_id` and branch on the presence of `room_id` plus `state`.

Frontend rules:

- Equipment cannot be bought twice.
- Selling consumes one owned entry.
- The starter armor cannot be sold.
- The player must keep at least one weapon and one armor when crafting; shop operations should also refresh from the returned authoritative profile.

## 8. Crafting

### `POST /api/game/craft`

Request:

```ts
interface CraftRequest {
  player_id: string;
  item1_type: ItemType;
  item1_id: string;
  item2_type: ItemType;
  item2_id: string;
}
```

Response:

```ts
interface CraftResult {
  id: string;
  name: string;
  desc: string;
  value: number;
  item_type: ItemType;
  type: ItemType;       // compatibility alias of item_type
  combat_stat: number;
  image_url: string;
}

interface CraftResponse {
  status: "success";
  result: CraftResult;
  profile: PlayerProfile;
}
```

The operation is atomic. Inputs are consumed only after generation succeeds. Replace the local profile with the returned profile.

### `GET /api/game/recipes`

```ts
interface RecipeRecord {
  ingredients: [
    { item_type: ItemType; item_id: string },
    { item_type: ItemType; item_id: string },
  ];
  mat1_id: string; // compatibility alias of ingredients[0].item_id
  mat2_id: string; // compatibility alias of ingredients[1].item_id
  result: CraftResult;
}

interface RecipesResponse {
  status: "success";
  recipes: RecipeRecord[];
}
```

Recipes are process-memory data and disappear on server restart.

## 9. Exploration and Maps

### Map contracts

```ts
export interface MapTemplate {
  template_id: string;
  world_id: string;
  region_id: string;
  scale: MapScale;
  width: number;
  height: number;
  terrain_weights: Record<string, number>;
  landmarks: Record<string, string>;
}

export interface MapCell {
  cell_id: number;
  x: number;
  y: number;
  terrain_id: string;
  landmark_id: string | null;
  passable: boolean;
  explored: boolean;
  gathered: boolean;
  is_gathered?: boolean;
}

export interface MapInstance {
  map_id: string;
  template_id: string;
  world_id: string;
  region_id: string;
  scale: MapScale;
  width: number;
  height: number;
  seed: number;
  config_version: string;
  current_cell_id: number;
  cells: MapCell[];
}

export interface TerrainDefinition {
  id: string;
  name: string;
  emoji: string;
  passable: boolean;
  drops: Record<string, number>;
}

export interface EncounterResult {
  encounter_id: string;
  npc_id: string;
  story_hook_id: string | null;
  trigger: "on_enter_map" | "on_enter_cell" | "on_gather";
}

export interface MapStateResponse {
  map: MapInstance;
  map_grid: MapCell[];
  terrains_meta: Record<string, TerrainDefinition>;
  resources_meta: Record<string, ResourceDefinition>;
  inventory_materials: Record<string, number>;
  encounter: EncounterResult | null;
}
```

Use `map_grid` for compatibility because it includes `is_gathered`. The canonical field is `gathered`; normalize both into one frontend field.

### `GET /api/map/templates`

```ts
interface MapTemplatesResponse {
  templates: MapTemplate[];
  worlds: Record<string, {
    world_id: string;
    name: string;
  }>;
  regions: Record<string, {
    region_id: string;
    world_id: string;
    name: string;
  }>;
}
```

Configured defaults:

- `small`: 5 x 5
- `medium`: 10 x 10
- `large`: 20 x 20
- `custom`: explicit dimensions

### `POST /api/map/enter`

```ts
interface EnterMapRequest {
  player_id: string;
  template_id?: string | null;
  refresh?: boolean;
  seed?: number | null;
}
```

Returns `MapStateResponse`.

- Omit `template_id` to enter the default map.
- `refresh: false` resumes the existing instance when the template matches.
- `refresh: true` creates a new instance and resets exploration/gathering progress.
- `seed` exists for deterministic generation and debugging; normal UI should omit it.

### `POST /api/map/move`

```ts
interface CellRequest {
  player_id: string;
  cell_id: number;
}
```

Returns `MapStateResponse`. Movement is orthogonal, one cell per request. Reject diagonals, non-adjacent destinations, and `passable: false` cells in the UI before sending.

### `POST /api/map/gather`

Uses `CellRequest`.

```ts
interface GatherResponse {
  status: "success";
  msg: string;
  loot: Record<string, number>;
  cell_id: number;
  inventory_materials: Record<string, number>;
  map: MapInstance;
  encounter: EncounterResult | null;
}
```

The UI should only offer gathering on the current cell and when it is not already gathered. On success, replace material quantities and map state from the response.

### Encounter flow

When any map response contains an encounter:

1. Pause or visually interrupt normal exploration.
2. Fetch `GET /api/world/npcs/{npc_id}?player_id=<player_id>`.
3. Show the NPC encounter card and relationship state.
4. Open dialogue using `POST /api/world/npcs/{npc_id}/dialogue`.
5. If dialogue returns `combat_trigger`, offer the battle entry action.

An encounter does not automatically start combat.

## 10. NPCs, Relationships, and Memory

```ts
export interface PublicNpc {
  npc_id: string;
  name: string;
  title: string;
  gender: string;
  race: string;
  appearance: string;
  location: {
    region: string;
    terrain_id: string | null;
    landmark: string;
    cell_ids: number[];
  };
  personality: string[];
  conversation_style: string;
  public_backstory: string;
  has_combat_profile: boolean;
  combat_threat: number | null;
}

export interface NpcRelationship {
  npc_id: string;
  player_id: string;
  affinity: number;
  trust: number;
  respect: number;
  hostility: number;
  flags: string[];
  active_story_hooks: string[];
  armed_combat_triggers: string[];
  consumed_combat_triggers: string[];
  interaction_count: number;
}

export interface StoryHook {
  hook_id: string;
  title: string;
  summary: string;
  min_affinity: number;
  min_trust: number;
  requires_memory_tags: string[];
}

export interface CombatTrigger {
  trigger_id: string;
  title: string;
  intro: string;
}

export interface MemoryEntry {
  memory_id: string;
  owner_type: "player" | "npc" | "world";
  owner_id: string;
  counterpart_id: string | null;
  summary: string;
  tags: string[];
  importance: number;
  facts: Record<string, unknown>;
  created_at: string;
}
```

### `GET /api/world/npcs`

Optional query parameters: `terrain_id`, `cell_id`.

```ts
interface NpcListResponse {
  npcs: PublicNpc[];
}
```

### `GET /api/world/npcs/{npc_id}`

- Without `player_id`: `{ npc: PublicNpc }`
- With `?player_id=...`: `{ npc: PublicNpc, relationship: NpcRelationship }`

Use the player-scoped form for all interactive NPC screens.

### `POST /api/world/npcs/{npc_id}/dialogue`

Request:

```ts
interface NpcDialogueRequest {
  player_id: string;
  message: string; // 1..500 characters
}
```

Response:

```ts
interface NpcDialogueResponse {
  npc_id: string;
  reply: string;
  tone: string;
  intent: string;
  relationship: NpcRelationship;
  activated_story_hook: StoryHook | null;
  combat_trigger: CombatTrigger | null;
}
```

Replace the relationship state after every dialogue. A non-null `combat_trigger` is the only valid frontend signal for enabling NPC combat.

### Memory endpoints

- `GET /api/world/npcs/{npc_id}/memories?player_id=...`
- `GET /api/world/players/{player_id}/memories`
- `GET /api/world/facts`

Responses:

```ts
interface NpcMemoriesResponse {
  npc_id: string;
  memories: MemoryEntry[];
}

interface PlayerMemoriesResponse {
  player_id: string;
  memories: MemoryEntry[];
}

interface WorldFactsResponse {
  facts: MemoryEntry[];
}
```

`POST /api/world/npcs/seed-demo` exists for development but is not required in the normal frontend bootstrap; NPCs are seeded when the application container starts.

## 11. Room HTTP API

### PvE room

```text
POST /api/room/create { player_id }
        -> { room_id }
POST /api/room/add_ai { room_id }
        -> { room_id, mode: "PvE" }
CONNECT /ws/room/{room_id}/{player_id}
SEND prep
SEND combat actions until game_over
```

### PvP room

```text
Player 1: POST /api/room/create { player_id }
          -> share room_id
Player 2: POST /api/room/join { room_id, player_id }
Both:     CONNECT /ws/room/{room_id}/{player_id}
Server:   broadcasts game_start after both sockets attach
Both:     SEND prep
Both:     SEND combat actions until game_over
```

Endpoint contracts:

```ts
// POST /api/room/create
type CreateRoomRequest = { player_id: string };
type CreateRoomResponse = { room_id: string };

// POST /api/room/join
type JoinRoomRequest = { room_id: string; player_id: string };
type JoinRoomResponse = { room_id: string; mode: "PvP" };

// POST /api/room/add_ai
type AddAiRequest = { room_id: string };
type AddAiResponse = { room_id: string; mode: "PvE" };
```

## 12. NPC Combat Entry

### `POST /api/world/npcs/{npc_id}/combat/start`

The `trigger_id` must come from the latest NPC dialogue response and must still be armed on the relationship.

```ts
interface NpcCombatStartRequest {
  player_id: string;
  trigger_id: string;
}

interface NpcCombatStartResponse {
  status: "success";
  room_id: string;
  websocket_path: string;
  snapshot: CombatSnapshot;
}
```

Connect to `websocket_path`, retain the HTTP snapshot as initial state, and continue with the standard prep/action protocol. Combat outcome automatically updates NPC memories and relationship values.

## 13. WebSocket Combat Protocol

### Connection

```text
GET ws://<host>:<port>/ws/room/{room_id}/{player_id}
```

The server accepts the socket before validating membership. Invalid members receive an error event and close code `1008`.

### Client messages

Preparation:

```ts
interface PrepMessage {
  action: "prep";
  weapon_id: string;
  armor_id: string;
  item_id?: string | null;
}
```

All equipment is validated against the player's server inventory.

Combat action:

```ts
interface CombatActionMessage {
  action: "combat";
  action_key: "0" | "9" | "i" | string;
}
```

Action keys:

- `"0"`: normal attack
- `"9"`: defense
- `"i"`: use selected combat item
- weapon skill ID, such as `"1"`: use that skill

Disable skills when `cost > current MP`. Disable `"i"` when `player_item_count <= 0`.

### Server events

```ts
type CombatServerEvent =
  | { event: "game_start"; snapshot: CombatSnapshot }
  | { event: "player_ready"; player_id: string; phase: "prep" | "combat" }
  | { event: "snapshot"; snapshot: CombatSnapshot }
  | { event: "error"; msg: string };
```

`player_ready` means the server accepted that player's submission, not that the phase has completed. Wait for `snapshot` before advancing the UI.

### Combat snapshot

```ts
export interface CombatState {
  environment: string | null;
  turn_count: number;
  game_mode: GameMode;
  p1_id: string;
  p2_id: string | null;

  player_gold: number;
  player_inventory: Inventory;
  player_class: CharacterDefinition | null;
  player_weapon: WeaponDefinition | null;
  player_armor: ArmorDefinition | null;
  player_item: ConsumableDefinition | null;
  player_item_id: string | null;
  player_item_count: number;
  player_hp: number;
  player_mp: number;
  player_status: string;

  ai_gold: number;
  ai_inventory: Inventory | Record<string, never>;
  ai_class: CharacterDefinition | null;
  ai_weapon: WeaponDefinition | null;
  ai_armor: ArmorDefinition | null;
  ai_item: ConsumableDefinition | null;
  ai_item_id: string | null;
  ai_item_count: number;
  ai_hp: number;
  ai_mp: number;
  ai_status: string;
}

export interface CombatSnapshot {
  room_id: string;
  thread_id: string;
  next_node: "PlayerPrep" | "PlayerAction" | null | string;
  game_over: boolean;
  state: CombatState;
  combat_log: string;
  npc_enemy: (PublicNpc & { relationship: NpcRelationship }) | null;
}
```

Snapshot handling:

- `next_node === "PlayerPrep"`: show loadout selection.
- `next_node === "PlayerAction"`: show action controls.
- `game_over === true`: disable actions and render final log/rewards.
- Replace combat state wholesale on every snapshot.
- Synchronize the main player store from `player_gold` and `player_inventory`.

The `combat_log` is already formatted player-facing text and may contain line breaks and emoji. Render it with preserved whitespace.

### Disconnect behavior

Any WebSocket disconnect removes the entire room. The other participant receives an error event. The frontend must return to the room lobby and cannot reconnect to the removed room.

## 14. HTTP Error Handling

Current error mapping:

| HTTP status | Meaning |
|---|---|
| `400` | Domain validation failure or invalid runtime state |
| `403` | Player is not authorized for the room/action |
| `404` | Player, room, NPC, or other keyed resource was not found |
| `422` | Request body/query validation failed |
| `500` | Unexpected server failure |

Domain errors generally return:

```json
{ "detail": "面向玩家的错误信息" }
```

Pydantic/FastAPI validation errors return `detail` as an array. The API client must support both shapes.

There are no stable machine-readable error codes yet. Do not branch business logic by comparing translated Chinese messages; use HTTP status plus local request context.

## 15. Suggested Frontend User Flows

### New session

```text
Load game meta
    -> choose character
    -> create profile
    -> retain player_id/profile
    -> enter hub
```

### Exploration

```text
Load map templates
    -> enter/resume map
    -> render width x height grid
    -> move to adjacent passable cell
    -> optionally gather current cell
    -> if encounter: load player-scoped NPC view
    -> dialogue
    -> optional NPC combat
```

### PvE battle

```text
Create room
    -> add AI
    -> connect WebSocket
    -> submit owned loadout
    -> submit action
    -> consume snapshots
    -> synchronize rewards/inventory
```

### NPC battle

```text
Encounter or NPC list
    -> dialogue
    -> receive combat_trigger
    -> start NPC combat over HTTP
    -> retain initial snapshot
    -> connect returned websocket_path
    -> standard prep/action loop
    -> refresh NPC relationship/memories after game over
```

## 16. Known Backend Constraints

The frontend should account for these current limitations:

1. All player, room, map, recipe, NPC relationship, memory, and checkpoint data is in process memory.
2. Server restart invalidates existing `player_id`, rooms, recipes, progress, and relationships.
3. Multi-worker deployment is unsupported.
4. There is no authentication or authorization token beyond path/body `player_id`.
5. There is no player restore/profile endpoint.
6. There are no stable machine-readable error codes.
7. WebSocket rooms are deleted on any disconnect; reconnect/resume is unsupported.
8. `POST /api/room/add_ai` can emit `game_start` before the client socket connects. The frontend must not depend on receiving that event for PvE.
9. Some HTTP responses are not yet declared as explicit FastAPI response models. Use the contracts in this document and verify against `/openapi.json` during integration.
10. `map.cells` and `map_grid` currently duplicate cell data; normalize them in the API adapter.

## 17. Environment Configuration

Recommended frontend environment variables:

```dotenv
VITE_RPG_API_BASE=http://127.0.0.1:8008
VITE_RPG_WS_BASE=ws://127.0.0.1:8008
```

Do not hardcode production hosts or derive `ws://` by string replacement without accounting for HTTPS. A safe adapter maps:

- `http:` to `ws:`
- `https:` to `wss:`

The backend CORS default allows all origins. Production should configure `CORS_ORIGINS` explicitly.

## 18. Migration Order

1. Introduce typed API and WebSocket adapters.
2. Replace old embedded catalog/config data with `/api/game/meta`.
3. Migrate player/inventory store to the new `PlayerProfile` and quantity maps.
4. Adapt character creation, shop, and crafting.
5. Implement scalable map templates and dynamic grid dimensions.
6. Implement encounter presentation and player-scoped NPC views.
7. Implement dialogue, relationship, story hook, and combat-trigger UI.
8. Replace the old combat socket protocol with event-discriminated messages and snapshots.
9. Add loading, validation, error, disconnect, and server-restart recovery states.
10. Run an end-to-end flow covering character creation through NPC combat settlement.

## 19. Acceptance Checklist

- [ ] No backend URL is hardcoded outside environment/config adapters.
- [ ] Catalog IDs are normalized as strings.
- [ ] Character creation persists `player_id` and profile state.
- [ ] Inventory supports quantity maps for items and materials.
- [ ] Shop and crafting replace local state from server responses.
- [ ] Maps render arbitrary `width` and `height`, including 20 x 20.
- [ ] Movement only enables adjacent passable cells.
- [ ] Gathering uses the current cell and disables depleted cells.
- [ ] Encounters open the correct NPC using `npc_id`.
- [ ] NPC dialogue updates relationship and story state.
- [ ] NPC combat is enabled only by a returned `combat_trigger`.
- [ ] Combat WebSocket handles every documented event.
- [ ] Combat actions use stable action keys.
- [ ] Final snapshots synchronize gold and inventory.
- [ ] HTTP 400/403/404/422 and WebSocket errors have visible UI states.
- [ ] Disconnect returns the user to a recoverable lobby state.
- [ ] Page refresh and server restart do not leave the UI permanently stuck.
- [ ] The complete character -> exploration -> encounter -> dialogue -> combat flow is verified.

## 20. When Backend Access Is Still Needed

The frontend migration should begin from this document alone. Inspect the backend only when one of these occurs:

- an actual response differs from this contract;
- `/openapi.json` changes;
- a missing endpoint blocks a required UX;
- WebSocket timing or state transitions behave differently during integration;
- the frontend requires a new backend capability rather than an adapter change.

When that happens, inspect only the affected router/schema/service instead of rescanning the repository.
