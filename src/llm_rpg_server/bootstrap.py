from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.combat import CombatEngine, CombatSessionService, InMemoryRoomRepository
from llm_rpg_server.crafting import (
    CraftingService,
    InMemoryRecipeRepository,
    LLMCraftDecisionGenerator,
    OpenAIItemImageGenerator,
)
from llm_rpg_server.exploration import ExplorationService
from llm_rpg_server.monsters import MonsterCatalog
from llm_rpg_server.npcs import InMemoryWorldRepository, NPCDialogueService, NPCInteractionService
from llm_rpg_server.npcs.loader import seed_npcs
from llm_rpg_server.players import EconomyService, InMemoryPlayerRepository, PlayerService
from llm_rpg_server.shared.config import LocalContentProvider, Settings
from llm_rpg_server.shared.llm import create_llm
from llm_rpg_server.shared.observability import Observability
from llm_rpg_server.world import EncounterService
from llm_rpg_server.world.events import WorldEventCoordinator


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    content: LocalContentProvider
    catalog: Catalog
    players: InMemoryPlayerRepository
    player_service: PlayerService
    economy: EconomyService
    world_repository: InMemoryWorldRepository
    npc_interactions: NPCInteractionService
    exploration: ExplorationService
    encounters: EncounterService
    monsters: MonsterCatalog
    world_events: WorldEventCoordinator
    recipes: InMemoryRecipeRepository
    crafting: CraftingService
    rooms: InMemoryRoomRepository
    combat: CombatSessionService
    observability: Observability


def build_container() -> AppContainer:
    load_dotenv()
    settings = Settings.from_env()
    content = LocalContentProvider(settings.content_root)
    content.validate()
    catalog = Catalog(content)
    players = InMemoryPlayerRepository()
    player_service = PlayerService(players, catalog, content)
    economy = EconomyService(players, catalog, content)
    llm = create_llm(settings)
    world_repository = InMemoryWorldRepository()
    seed_npcs(world_repository, content)
    npc_dialogue = NPCDialogueService(content, llm)
    npc_interactions = NPCInteractionService(world_repository, npc_dialogue, content)
    exploration = ExplorationService(players, catalog, content)
    monsters = MonsterCatalog(content, catalog)
    encounters = EncounterService(content, npc_interactions, clock=exploration.clock)
    exploration.set_encounter_resolver(encounters)
    economy.set_access_policy(exploration)
    recipes = InMemoryRecipeRepository()
    crafting = CraftingService(
        players,
        catalog,
        recipes,
        content,
        LLMCraftDecisionGenerator(content, llm),
        OpenAIItemImageGenerator(content, settings),
    )
    rooms = InMemoryRoomRepository()
    observability = Observability()
    combat_engine = CombatEngine(catalog, players, player_service, content, llm)
    combat = CombatSessionService(
        combat_engine,
        rooms,
        players,
        catalog,
        npc_interactions,
        content,
        observability,
        monsters,
    )
    world_events = WorldEventCoordinator(exploration, npc_interactions, monsters, combat)
    exploration.set_event_participant_resolver(world_events)
    container = AppContainer(
        settings=settings,
        content=content,
        catalog=catalog,
        players=players,
        player_service=player_service,
        economy=economy,
        world_repository=world_repository,
        npc_interactions=npc_interactions,
        exploration=exploration,
        encounters=encounters,
        monsters=monsters,
        world_events=world_events,
        recipes=recipes,
        crafting=crafting,
        rooms=rooms,
        combat=combat,
        observability=observability,
    )
    validate_references(container)
    return container


def validate_references(container: AppContainer) -> None:
    catalog = container.catalog
    for npc in container.world_repository.list_npcs():
        equipment = npc.equipment
        if equipment.weapon_id and equipment.weapon_id not in catalog.weapons:
            raise ValueError(f"NPC {npc.npc_id} references unknown weapon {equipment.weapon_id}")
        if equipment.armor_id and equipment.armor_id not in catalog.armors:
            raise ValueError(f"NPC {npc.npc_id} references unknown armor {equipment.armor_id}")
        if any(item_id not in catalog.items for item_id in equipment.items):
            raise ValueError(f"NPC {npc.npc_id} references an unknown item")
        if npc.combat and (
            npc.combat.character_id not in catalog.characters
            or npc.combat.weapon_id not in catalog.weapons
            or npc.combat.armor_id not in catalog.armors
            or (npc.combat.item_id is not None and npc.combat.item_id not in catalog.items)
        ):
            raise ValueError(f"NPC {npc.npc_id} has an invalid combat profile")
    npc_ids = {npc.npc_id for npc in container.world_repository.list_npcs()}
    for rule in container.encounters.rules:
        if rule.npc_id not in npc_ids:
            raise ValueError(f"Encounter {rule.encounter_id} references unknown NPC {rule.npc_id}")
        npc = container.world_repository.get_npc(rule.npc_id)
        hook_ids = {hook.hook_id for hook in npc.story_hooks}
        if rule.story_hook_id and rule.story_hook_id not in hook_ids:
            raise ValueError(f"Encounter {rule.encounter_id} references unknown story hook {rule.story_hook_id}")
        missing_regions = set(rule.locations.region_ids) - set(container.exploration.regions)
        missing_templates = set(rule.locations.map_template_ids) - set(container.exploration.templates)
        missing_terrains = set(rule.locations.terrain_ids) - set(container.exploration.terrains)
        if missing_regions or missing_templates or missing_terrains:
            raise ValueError(f"Encounter {rule.encounter_id} contains invalid location references")
    terrain_ids = set(container.exploration.terrains)
    for template in container.exploration.templates.values():
        missing = set(template.terrain_weights) - terrain_ids
        if missing:
            raise ValueError(f"Map template {template.template_id} references unknown terrain: {sorted(missing)}")
