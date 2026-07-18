from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictInt


class CreateCharacterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    race_id: str


class AttributeAllocationRequest(BaseModel):
    player_id: str
    allocations: dict[str, StrictInt]


class QuestCompleteRequest(BaseModel):
    player_id: str
    npc_id: str
    hook_id: str


class PlayerRequest(BaseModel):
    player_id: str


class TradeRequest(BaseModel):
    player_id: str
    thread_id: str | None = None
    item_type: Literal["weapon", "armor", "item", "material"]
    item_id: str


class CraftRequest(BaseModel):
    player_id: str
    item1_type: Literal["weapon", "armor", "item", "material"]
    item1_id: str
    item2_type: Literal["weapon", "armor", "item", "material"]
    item2_id: str


class EnterMapRequest(BaseModel):
    player_id: str
    template_id: str | None = None
    refresh: bool = False
    seed: int | None = None


class CellRequest(BaseModel):
    player_id: str
    cell_id: int = Field(ge=0)


class DirectionRequest(BaseModel):
    player_id: str
    direction: Literal["up", "down", "left", "right"]


class EatRequest(BaseModel):
    player_id: str
    item_id: str


class EventActionRequest(BaseModel):
    player_id: str
    event_id: str
    action_id: str


class RoomJoinRequest(BaseModel):
    room_id: str
    player_id: str


class AddAIRequest(BaseModel):
    room_id: str


class NPCDialogueRequest(BaseModel):
    player_id: str
    message: str = Field(min_length=1, max_length=500)


class NPCCombatStartRequest(BaseModel):
    player_id: str
    trigger_id: str
