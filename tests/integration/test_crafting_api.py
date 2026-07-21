from fastapi.testclient import TestClient

from llm_rpg_server.bootstrap import build_container
from llm_rpg_server.crafting import (
    CraftArtwork,
    CraftCategoryCatalog,
    CraftConcept,
    CraftingService,
    CraftingWorkflow,
    CraftPropertyProposal,
)
from llm_rpg_server.main import create_app


class _ConceptGenerator:
    def generate(self, first, second, result_type, allowed_categories):
        return CraftConcept(
            success=True,
            reason="The source ingredients have a coherent alchemical path.",
            name="API Test Tonic",
            desc="A tonic made by the API contract test.",
            category="potion",
        )


class _PropertyGenerator:
    def generate(self, first, second, concept, property_contract, prompt_id):
        return CraftPropertyProposal(properties={
            "effect_type": "heal_hp",
            "val": 40,
            "stamina_restore": 0,
            "clear_negative_statuses": False,
        })


class _ArtworkGenerator:
    def generate(self, first, second, concept):
        return CraftArtwork(image_key="outputs/crafting/api-contract.png", status="generated")


def test_successful_craft_has_one_success_recipe_and_preserves_image_key():
    container = build_container()
    workflow = CraftingWorkflow(
        CraftCategoryCatalog(container.content),
        _ConceptGenerator(),
        _ArtworkGenerator(),
        _PropertyGenerator(),
    )
    container.crafting = CraftingService(
        container.players,
        container.catalog,
        container.recipes,
        container.content,
        workflow,
    )
    profile = container.player_service.create("API Crafter", "1")
    with container.players.transaction(profile.player_id) as current:
        current.inventory.items["2"] = 1

    with TestClient(create_app(container)) as client:
        response = client.post("/api/game/craft", json={
            "player_id": profile.player_id,
            "item1_type": "item",
            "item1_id": "1",
            "item2_type": "item",
            "item2_id": "2",
        })
        recipes = client.get("/api/game/recipes")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["result"]["image_key"] == "outputs/crafting/api-contract.png"
    assert payload["result"]["properties"]["val"] == 40
    assert recipes.status_code == 200
    assert [record["success"] for record in recipes.json()["recipes"]] == [True]
