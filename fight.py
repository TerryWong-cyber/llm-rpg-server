# fight.py
import os
import sys
import uuid
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# 导入 Langfuse 依赖
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

from models_and_state import GameState
from agent_nodes import (
    init_game_node, player_select_prep_node, round_start_node,
    player_action_node, ai_action_node, judge_node, settlement_node,
    CHARACTERS, WEAPONS, ARMORS, ITEMS
)

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "whatever")
os.environ["OPENAI_API_BASE"] = "http://127.0.0.1:5057/v1"

game_session_id = f"rpg_session_{uuid.uuid4().hex[:10]}"

# 初始化 Langfuse 客户端与回调处理器（暴露给 server.py 引入）
langfuse = get_client()
langfuse_handler = CallbackHandler()

llm = ChatOpenAI(model="qwen3-vl-8b-instruct", temperature=0.5)


def safe_input(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    return sys.stdin.readline().strip()


# ==========================================
# 组装 LangGraph
# ==========================================
workflow = StateGraph(GameState)

workflow.add_node("Init", init_game_node)
workflow.add_node("PlayerPrep", player_select_prep_node)
workflow.add_node("RoundStart", round_start_node)
workflow.add_node("PlayerAction", player_action_node)
workflow.add_node("AIAction", ai_action_node)

# 确保大模型节点使用 lambda 接收并透传 config，激活 Langfuse 追踪
workflow.add_node("Judge", lambda state, config: judge_node(state, llm, config))
workflow.add_node("Settlement", lambda state, config: settlement_node(state, llm, config))

workflow.set_entry_point("Init")
workflow.add_edge("Init", "PlayerPrep")
workflow.add_edge("PlayerPrep", "RoundStart")
workflow.add_edge("RoundStart", "PlayerAction")
workflow.add_edge("RoundStart", "AIAction")
workflow.add_edge(["PlayerAction", "AIAction"], "Judge")


def check_game_over(state: GameState):
    if state["player_hp"] <= 0 or state["ai_hp"] <= 0: return "Settlement"
    return "RoundStart"


workflow.add_conditional_edges("Judge", check_game_over)
workflow.add_edge("Settlement", END)

memory = MemorySaver()
fight_app = workflow.compile(
    checkpointer=memory,
    interrupt_before=["PlayerPrep", "PlayerAction"]
)

# ==========================================
# 单机终端运行模式 (自带完整的 Langfuse 追踪机制)
# ==========================================
if __name__ == "__main__":
    print("\n⚔️ 本地终端模拟器启动 ⚔️")

    # 模拟外部传入的 config
    config = {
        "configurable": {"thread_id": game_session_id},
        "callbacks": [langfuse_handler],  # 注入回调
        "run_name": "CLI_Battle_Mode"
    }

    # 单机一镜到底跑法：用 with 上下文包裹整个进程
    with langfuse.start_as_current_observation(as_type="span", name="Epic_Battle_Game"):
        with propagate_attributes(session_id=game_session_id):

            # 1. 启动游戏初始化
            fight_app.invoke({
                "messages": [],
                "player_id": "cli_player_1",
                "p2_id": "AI_BOT",
                "game_mode": "PvE"
            }, config=config)

            # (简化的终端交互流测试，仅供验证图流转)...
            state = fight_app.get_state(config)
            print("初始化完毕，进入战前准备环节...")

            # 补齐状态模拟继续运行
            fight_app.update_state(config, {
                "player_class": CHARACTERS["warrior"], "player_weapon": WEAPONS["rusty_sword"],
                "player_hp": 100, "player_mp": 50,
                "ai_class": CHARACTERS["mage"], "ai_weapon": WEAPONS["magic_staff"],
                "ai_hp": 80, "ai_mp": 100
            })

            for _ in fight_app.stream(None, config=config): pass

            print("\n进入战斗...")
            fight_app.update_state(config, {
                "player_action": {"id": "0", "name": "普通攻击", "cost": 0, "type": "attack", "multiplier": 1.0},
                "ai_action": {"id": "0", "name": "普通攻击", "cost": 0, "type": "attack", "multiplier": 1.0}
            })

            for _ in fight_app.stream(None, config=config): pass
            print(fight_app.get_state(config).values.get("messages")[-1].content)

    langfuse.flush()
    print("\n[系统] 模拟运行结束，Langfuse 日志已上传。")