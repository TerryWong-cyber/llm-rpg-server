from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from llm_rpg_server.api.dependencies import container_from_request
from llm_rpg_server.api.schemas import CraftRequest, CreateCharacterRequest, TradeRequest
from llm_rpg_server.bootstrap import AppContainer
from llm_rpg_server.crafting import ItemReference

router = APIRouter(prefix="/api/game", tags=["game"])
Container = Annotated[AppContainer, Depends(container_from_request)]


@router.get("/meta")
def game_meta(container: Container):
    return container.catalog.public_view()


@router.post("/character/create")
def create_character(request: CreateCharacterRequest, container: Container):
    profile = container.player_service.create(request.name, request.character_id)
    return {"status": "success", "player_id": profile.player_id, "profile": profile.model_dump(mode="json")}


@router.post("/shop/buy")
def buy_item(request: TradeRequest, container: Container):
    profile = container.economy.buy(request.player_id, request.item_type, request.item_id)
    return _profile_or_snapshot(container, request.thread_id, profile.model_dump(mode="json"))


@router.post("/shop/sell")
def sell_item(request: TradeRequest, container: Container):
    profile = container.economy.sell(request.player_id, request.item_type, request.item_id)
    return _profile_or_snapshot(container, request.thread_id, profile.model_dump(mode="json"))


@router.post("/craft")
def craft_item(request: CraftRequest, container: Container):
    attempt = container.crafting.craft(
        request.player_id,
        ItemReference(item_type=request.item1_type, item_id=request.item1_id),
        ItemReference(item_type=request.item2_type, item_id=request.item2_id),
    )
    profile = container.players.get(request.player_id)
    if not attempt.success:
        return {
            "status": "failed",
            "result": None,
            "failure_reason": attempt.failure_reason,
            "profile": profile.model_dump(mode="json"),
        }
    result = attempt.result
    if result is None:
        raise RuntimeError(container.content.text("errors.craft.failed"))
    return {
        "status": "success",
        "result": result.public_dict(),
        "failure_reason": "",
        "profile": profile.model_dump(mode="json"),
    }


@router.get("/recipes")
def recipes(container: Container):
    return {"status": "success", "recipes": container.recipes.list_all()}


def _profile_or_snapshot(container: AppContainer, thread_id: str | None, profile: dict):
    if thread_id:
        room = container.rooms.find_by_thread(thread_id)
        if room:
            return container.combat.snapshot(room)
    return {"status": "success", "profile": profile}
