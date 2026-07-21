from __future__ import annotations

from dataclasses import dataclass

from dotenv import load_dotenv

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.combat import CombatEngine, CombatSessionService, InMemoryRoomRepository
from llm_rpg_server.crafting import (
    CraftCategoryCatalog,
    CraftingService,
    CraftingWorkflow,
    InMemoryRecipeRepository,
    LLMCraftConceptGenerator,
    LLMCraftPropertyGenerator,
    OssCraftArtworkGenerator,
)
from llm_rpg_server.exploration import ExplorationService
from llm_rpg_server.items import ItemService
from llm_rpg_server.monsters import MonsterCatalog
from llm_rpg_server.npcs import InMemoryWorldRepository, NPCDialogueService, NPCInteractionService
from llm_rpg_server.npcs.loader import seed_npcs
from llm_rpg_server.players import (
    CharacterChronicleService,
    EconomyService,
    GrowthService,
    InMemoryPlayerRepository,
    PlayerService,
    ResourceLifecycleService,
)
from llm_rpg_server.shared.config import LocalContentProvider, Settings
from llm_rpg_server.shared.llm import create_llm
from llm_rpg_server.shared.observability import Observability
from llm_rpg_server.skills import SkillCatalog, SkillService
from llm_rpg_server.world import EncounterService
from llm_rpg_server.world.events import WorldEventCoordinator


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    content: LocalContentProvider
    catalog: Catalog
    players: InMemoryPlayerRepository
    player_service: PlayerService
    chronicle: CharacterChronicleService
    growth: GrowthService
    economy: EconomyService
    items: ItemService
    skill_catalog: SkillCatalog
    skills: SkillService
    world_repository: InMemoryWorldRepository
    npc_interactions: NPCInteractionService
    exploration: ExplorationService
    resources: ResourceLifecycleService
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
    growth = GrowthService(players, player_service, content)
    economy = EconomyService(players, catalog, content)
    items = ItemService(players, catalog, content)
    skill_catalog = SkillCatalog(content)
    skills = SkillService(players, skill_catalog, catalog, content)
    growth.set_skill_listeners(skills.apply_level_unlocks, skills.grant_quest_rewards)
    llm = create_llm(settings)
    world_repository = InMemoryWorldRepository()
    seed_npcs(world_repository, content)
    npc_dialogue = NPCDialogueService(content, llm)
    npc_interactions = NPCInteractionService(world_repository, npc_dialogue, content, players)
    npc_interactions.set_story_hook_listener(growth.start_quest)
    exploration = ExplorationService(players, catalog, content)
    exploration.add_event_rules(skill_catalog.world_events())
    chronicle = CharacterChronicleService(players, content, exploration.clock)
    player_service.set_chronicle(chronicle)
    growth.set_chronicle(chronicle)
    skills.set_chronicle(chronicle)
    exploration.set_chronicle(chronicle)
    growth.set_clock(exploration.clock)
    resources = ResourceLifecycleService(players, exploration.clock)
    exploration.set_resource_lifecycle(resources)
    monsters = MonsterCatalog(content, catalog)
    encounters = EncounterService(content, npc_interactions, clock=exploration.clock)
    exploration.set_encounter_resolver(encounters)
    economy.set_access_policy(exploration)
    recipes = InMemoryRecipeRepository()
    crafting_categories = CraftCategoryCatalog(content)
    crafting_workflow = CraftingWorkflow(
        crafting_categories,
        LLMCraftConceptGenerator(content, llm),
        OssCraftArtworkGenerator(content, settings),
        LLMCraftPropertyGenerator(content, crafting_categories, llm),
    )
    crafting = CraftingService(
        players,
        catalog,
        recipes,
        content,
        crafting_workflow,
    )
    rooms = InMemoryRoomRepository()
    observability = Observability()
    combat_engine = CombatEngine(
        catalog,
        players,
        player_service,
        growth,
        content,
        llm,
        skills,
        world_clock=exploration.clock,
    )
    combat = CombatSessionService(
        combat_engine,
        rooms,
        players,
        catalog,
        npc_interactions,
        content,
        observability,
        growth,
        monsters,
        resources,
        skills,
        chronicle,
    )
    world_events = WorldEventCoordinator(exploration, npc_interactions, monsters, combat, items, skills)
    exploration.set_event_participant_resolver(world_events)
    container = AppContainer(
        settings=settings,
        content=content,
        catalog=catalog,
        players=players,
        player_service=player_service,
        chronicle=chronicle,
        growth=growth,
        economy=economy,
        items=items,
        skill_catalog=skill_catalog,
        skills=skills,
        world_repository=world_repository,
        npc_interactions=npc_interactions,
        exploration=exploration,
        resources=resources,
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
    skill_ids = set(container.skill_catalog.rules.skills)
    for item_id, item in catalog.items.items():
        learned_skill = item.get("learn_skill_id")
        if learned_skill and learned_skill not in skill_ids:
            raise ValueError(f"Skill book {item_id} references unknown skill {learned_skill}")
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
        for hook in npc.story_hooks:
            missing_skill_rewards = set(hook.skill_rewards) - skill_ids
            if missing_skill_rewards:
                raise ValueError(
                    f"Quest {hook.hook_id} references unknown skills: {sorted(missing_skill_rewards)}"
                )
            for requirement in hook.requirements:
                if requirement.kind == "region" and requirement.region_id not in container.exploration.regions:
                    raise ValueError(
                        f"Quest {hook.hook_id} references unknown region {requirement.region_id}"
                    )
                if requirement.kind == "inventory":
                    collection = (
                        catalog.resources
                        if requirement.item_type == "material"
                        else catalog.items
                    )
                    if requirement.item_id not in collection:
                        raise ValueError(
                            f"Quest {hook.hook_id} references unknown item {requirement.item_id}"
                        )
    npc_ids = {npc.npc_id for npc in container.world_repository.list_npcs()}
    missing_trainers = set(container.skill_catalog.rules.trainers) - npc_ids
    if missing_trainers:
        raise ValueError(f"Skill trainers reference unknown NPCs: {sorted(missing_trainers)}")
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
