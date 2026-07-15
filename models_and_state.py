# models_and_state.py
import operator
from typing import Annotated, Sequence, TypedDict, Dict, Any, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage


# ==========================================
# 1. Pydantic 模型：大模型现在只做“裁判”，不写死数值
# ==========================================
class CombatResult(BaseModel):
    effectiveness_score: int = Field(
        description="动作效果评分(0-10)。0为完全落空/被完全格挡，5为正常命中(100%伤害)，10为完美发挥/暴击(200%伤害)。防御/吃药动作通常评为5。"
    )
    status: str = Field(description="附加的异常状态(如'中毒', '眩晕', '减速')，如果无状态或抵抗了，输出'正常'")


class JudgeResult(BaseModel):
    combat_narration: str = Field(
        description="战报描述：基于环境、属性、装备进行推演的交锋画面（80字左右，需解释评分依据）")
    player_result: CombatResult
    ai_result: CombatResult


# ==========================================
# 2. 状态流转字典 (LangGraph State)
# ==========================================
def replace_state(current, update):
    return update if update is not None else current


class GameState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    environment: Annotated[str, replace_state]
    turn_count: Annotated[int, replace_state]

    # --- 关联数据主键 ---
    player_id: Annotated[str, replace_state]
    p2_id: Annotated[str, replace_state]    # 👈 新增：2P 玩家的 ID
    game_mode: Annotated[str, replace_state] # 👈 新增：PvE 还是 PvP

    # --- 玩家战斗内状态 (仅用于 fight 运行时) ---
    player_class: Annotated[Optional[Dict[str, Any]], replace_state]
    player_weapon: Annotated[Dict[str, Any], replace_state]
    player_armor: Annotated[Dict[str, Any], replace_state]
    player_item: Annotated[Optional[Dict[str, Any]], replace_state]
    player_item_count: Annotated[int, replace_state]

    player_hp: Annotated[int, replace_state]
    player_mp: Annotated[int, replace_state]
    player_status: Annotated[str, replace_state]
    player_action: Annotated[Dict[str, Any], replace_state]

    # --- AI / 2P 状态 ---
    ai_class: Annotated[Optional[Dict[str, Any]], replace_state]
    ai_weapon: Annotated[Dict[str, Any], replace_state]
    ai_armor: Annotated[Dict[str, Any], replace_state]
    ai_item: Annotated[Optional[Dict[str, Any]], replace_state]
    ai_item_count: Annotated[int, replace_state]

    ai_hp: Annotated[int, replace_state]
    ai_mp: Annotated[int, replace_state]
    ai_status: Annotated[str, replace_state]
    ai_action: Annotated[Dict[str, Any], replace_state]