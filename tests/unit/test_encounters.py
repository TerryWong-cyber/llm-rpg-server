from llm_rpg_server.exploration import ExplorationService
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
    encounter = None
    for cell_id in (1, 2, 7, 12):
        _, encounter = exploration.move(player.player_id, cell_id)
    assert encounter is not None
    assert encounter.npc_id == "evelyn_mossmark"
    assert encounter.story_hook_id == "evelyn_lost_spores"

