from pathlib import Path

import pytest

from llm_rpg_server.catalog import Catalog
from llm_rpg_server.npcs import InMemoryWorldRepository, NPCDialogueService, NPCInteractionService
from llm_rpg_server.npcs.loader import seed_npcs
from llm_rpg_server.shared.config import LocalContentProvider


@pytest.fixture
def content():
    root = Path(__file__).resolve().parents[1] / "configs"
    return LocalContentProvider(root)


@pytest.fixture
def catalog(content):
    return Catalog(content)


@pytest.fixture
def npc_service(content):
    repository = InMemoryWorldRepository()
    seed_npcs(repository, content)
    return NPCInteractionService(repository, NPCDialogueService(content, llm=None), content)
