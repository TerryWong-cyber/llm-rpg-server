from llm_rpg_server.exploration import ExplorationService, MapInstance
from llm_rpg_server.npcs import InMemoryWorldRepository, NPCDialogueService, NPCInteractionService
from llm_rpg_server.npcs.loader import seed_npcs
from llm_rpg_server.players import InMemoryPlayerRepository, PlayerService
from llm_rpg_server.world import EncounterService


def test_landmark_can_trigger_configured_npc_encounter(content, catalog):
    players = InMemoryPlayerRepository()
    player = PlayerService(players, catalog, content).create("Explorer", "1")
    world = InMemoryWorldRepository()
    seed_npcs(world, content)
    npcs = NPCInteractionService(world, NPCDialogueService(content, llm=None), content)
    exploration = ExplorationService(players, catalog, content)
    exploration.set_encounter_resolver(EncounterService(content, npcs))
    exploration.enter(player.player_id, "mistwood_small", seed=42)
    with players.transaction(player.player_id) as profile:
        current = MapInstance.model_validate(profile.current_map)
        current.current_cell_id = 11
        profile.current_map = current.model_dump(mode="json")
    _, encounter = exploration.move(player.player_id, 12)
    assert encounter is not None
    assert encounter.npc_id == "evelyn_mossmark"
    assert encounter.story_hook_id == "evelyn_lost_spores"
