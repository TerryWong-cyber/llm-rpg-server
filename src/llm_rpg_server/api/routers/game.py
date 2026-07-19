from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from llm_rpg_server.api.dependencies import container_from_request
from llm_rpg_server.api.schemas import (
    AttributeAllocationRequest,
    CraftRequest,
    CreateCharacterRequest,
    EquipmentRequest,
    QuestCompleteRequest,
    TradeRequest,
    UseItemRequest,
)
from llm_rpg_server.bootstrap import AppContainer
from llm_rpg_server.crafting import ItemReference

router = APIRouter(prefix="/api/game", tags=["game"])
Container = Annotated[AppContainer, Depends(container_from_request)]


@router.get("/meta")
def game_meta(container: Container):
    return container.catalog.public_view()


@router.post("/character/create")
def create_character(request: CreateCharacterRequest, container: Container):
    profile = container.player_service.create(request.name, request.race_id)
    return {"status": "success", "player_id": profile.player_id, "profile": profile.model_dump(mode="json")}


@router.post("/character/allocate")
def allocate_attributes(request: AttributeAllocationRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
    profile = container.growth.allocate(request.player_id, request.allocations)
    return {
        "status": "success",
        "profile": profile.model_dump(mode="json"),
        "progression": container.growth.public_progress(profile),
    }


@router.post("/character/equipment")
def set_equipment(request: EquipmentRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
    profile = container.player_service.set_equipment(
        request.player_id,
        request.item_type,
        request.item_id,
    )
    return {"status": "success", "profile": profile.model_dump(mode="json")}


@router.post("/quest/complete")
def complete_quest(request: QuestCompleteRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
    npc = container.world_repository.get_npc(request.npc_id)
    hook = next((item for item in npc.story_hooks if item.hook_id == request.hook_id), None)
    if hook is None:
        raise ValueError(container.content.text("errors.npc.story_hook_unknown"))
    reward = container.growth.complete_quest(
        request.player_id,
        request.npc_id,
        request.hook_id,
    )
    container.npc_interactions.complete_story_hook(
        request.npc_id,
        request.player_id,
        request.hook_id,
    )
    profile = container.players.get(request.player_id)
    return {
        "status": "success",
        "profile": profile.model_dump(mode="json"),
        "reward": reward,
    }


@router.post("/shop/buy")
def buy_item(request: TradeRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
    profile = container.economy.buy(request.player_id, request.item_type, request.item_id)
    return _profile_or_snapshot(container, request.thread_id, profile.model_dump(mode="json"))


@router.post("/shop/sell")
def sell_item(request: TradeRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
    profile = container.economy.sell(request.player_id, request.item_type, request.item_id)
    return _profile_or_snapshot(container, request.thread_id, profile.model_dump(mode="json"))


@router.post("/use-item")
def use_item(request: UseItemRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
    profile, outcome = container.items.use_outside_combat(request.player_id, request.item_id)
    return {
        "status": "success",
        "profile": profile.model_dump(mode="json"),
        "outcome": outcome.model_dump(mode="json"),
    }


@router.post("/craft")
def craft_item(request: CraftRequest, container: Container):
    container.resources.settle(request.player_id, interrupt_sleep=True)
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
