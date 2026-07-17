from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage


def replace_state(current: Any, update: Any) -> Any:
    return update if update is not None else current


class GameState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    environment: Annotated[str, replace_state]
    turn_count: Annotated[int, replace_state]
    player_id: Annotated[str, replace_state]
    p2_id: Annotated[str, replace_state]
    game_mode: Annotated[str, replace_state]
    reward_policy: Annotated[str, replace_state]
    player_class: Annotated[dict[str, Any] | None, replace_state]
    player_weapon: Annotated[dict[str, Any], replace_state]
    player_armor: Annotated[dict[str, Any], replace_state]
    player_item: Annotated[dict[str, Any] | None, replace_state]
    player_item_id: Annotated[str | None, replace_state]
    player_item_count: Annotated[int, replace_state]
    player_hp: Annotated[int, replace_state]
    player_mp: Annotated[int, replace_state]
    player_status: Annotated[str, replace_state]
    player_action: Annotated[dict[str, Any], replace_state]
    ai_class: Annotated[dict[str, Any] | None, replace_state]
    ai_weapon: Annotated[dict[str, Any], replace_state]
    ai_armor: Annotated[dict[str, Any], replace_state]
    ai_item: Annotated[dict[str, Any] | None, replace_state]
    ai_item_id: Annotated[str | None, replace_state]
    ai_item_count: Annotated[int, replace_state]
    ai_hp: Annotated[int, replace_state]
    ai_mp: Annotated[int, replace_state]
    ai_status: Annotated[str, replace_state]
    ai_action: Annotated[dict[str, Any], replace_state]
