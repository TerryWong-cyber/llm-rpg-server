# agent_nodes.py
import os
import json
import random
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from models_and_state import GameState, JudgeResult, CombatResult
from typing import Dict, Any

# 👈 从统一配置中心加载所有 Excel 翻译后的静态属性与公式常量
from config_loader import CHARACTERS, WEAPONS, ARMORS, ITEMS, ENVIRONMENTS, GLOBAL_CONFIGS

# 👈 Bug 1 额外加固：确保即使策划漏配，运行时全局配置中一定包含 0 号基础布衣，防止背包无法渲染
if "0" not in ARMORS:
    ARMORS["0"] = {
        "id": 0,
        "name": "平民布衣",
        "hp_bonus": 0,
        "def_rate": 0.0,
        "value": 0,
        "desc": "没有任何额外防御力的粗布便服。"
    }

# ==========================================
# 0.1 内存 Mock 玩家数据表 (后续无缝对接到 MySQL)
# ==========================================
MOCK_PLAYER_DB = {}


def create_player_profile(player_id: str, name: str, character_id: str) -> Dict[str, Any]:
    """模拟在 MySQL 玩家主表中 INSERT 一条新存档"""
    MOCK_PLAYER_DB[player_id] = {
        "player_id": player_id,
        "name": name,
        "character_id": character_id,
        "gold": GLOBAL_CONFIGS.get("initial_gold", 100),
        "inventory": {
            "weapons": ["1"],  # 初始默认只有 1 号精钢阔剑
            "armors": ["0"],  # 👈 【Bug 1 修复】：初始防具更正为 "0"号平民布衣，保障开局装配正常
            "items": {"1": 3},  # 初始只有 3 瓶治疗药水
            "materials": {}
        },
        "current_map": None  # 探索地图格子缓存
    }
    return MOCK_PLAYER_DB[player_id]


# ==========================================
# 核心节点交互逻辑
# ==========================================
def init_game_node(state: GameState):
    """初始化：兼容 PvP 待机与 PvE 生成"""
    player_id = state.get("player_id")
    game_mode = state.get("game_mode", "PvE")

    if player_id not in MOCK_PLAYER_DB:
        create_player_profile(player_id, "无名小卒", "1")

    profile = MOCK_PLAYER_DB[player_id]
    player_class = CHARACTERS[profile["character_id"]]

    base_state = {
        "environment": random.choice(ENVIRONMENTS),
        "player_class": player_class,  # 👈 【Bug 2 修复】：初始化即向图中写入 player_class，保证进入配装大厅时自动锁定职业
        "turn_count": 1,
    }

    # 如果是打电脑，立刻生成电脑属性；如果是 PvP，留空等待双方在 Prep 阶段提交
    if game_mode == "PvE":
        ai_class = random.choice(list(CHARACTERS.values()))
        ai_armor_list = [v for k, v in ARMORS.items() if k != "0"]
        base_state.update({
            "ai_class": ai_class,
            "ai_weapon": random.choice(list(WEAPONS.values())),
            "ai_armor": random.choice(ai_armor_list),
            "ai_item": random.choice(list(ITEMS.values())),
            "ai_item_count": 1,
            "ai_hp": ai_class["hp"] + random.choice(ai_armor_list)["hp_bonus"],
            "ai_mp": ai_class["mp"],
            "ai_status": "正常",
        })
    return base_state


def player_select_prep_node(state: GameState): return {}


def round_start_node(state: GameState): return {}


def player_action_node(state: GameState): return {}


def ai_action_node(state: GameState):
    """AI 动作节点：如果是 PvP 模式，动作已由 WebSockets 注入，直接跳过此节点"""
    if state.get("game_mode") == "PvP":
        return {}

    if state["ai_hp"] < state["ai_class"]["hp"] * 0.4 and state["ai_item_count"] > 0:
        action = {"id": "item", "name": f"使用物品【{state['ai_item']['name']}】", "cost": 0, "type": "item"}
    else:
        skill = random.choice(state["ai_weapon"]["skills"])
        if state["ai_mp"] >= skill["cost"] and random.random() > 0.3:
            action = {"id": skill["id"], "name": f"技能【{skill['name']}】", "cost": skill["cost"], "type": "skill",
                      "multiplier": skill["multiplier"]}
        else:
            action = {"id": "0", "name": "基础普通攻击", "cost": 0, "type": "attack", "multiplier": 1.0}
    return {"ai_action": action}


def calculate_base_dmg(char, weapon, action):
    """物理与魔法双轨制的代码计算公式（调节系数完全源于 Excel 表）"""
    if action["type"] in ["defense", "item"]: return 0
    base = weapon["base_dmg"]
    phys_coeff = GLOBAL_CONFIGS.get("phys_attr_coeff", 1.5)
    magic_coeff = GLOBAL_CONFIGS.get("magic_attr_coeff", 1.5)
    attr_bonus = char["str"] * phys_coeff if weapon["type"] == "phys" else char["int"] * magic_coeff
    return (base + attr_bonus) * action.get("multiplier", 1.0)


def process_action_effects(actor_state_prefix, target_state_prefix, action, score, state, items, armors):
    """数值核心计算引擎"""
    hp_change = 0
    mp_change = -action["cost"]
    dmg_dealt = 0

    # 1. 物品使用逻辑
    if action["type"] == "item":
        item_cfg = items[state[f"{actor_state_prefix}_item"]["name"]]
        if item_cfg["type"] == "heal_hp":
            hp_change += item_cfg["val"]
        elif item_cfg["type"] == "heal_mp":
            mp_change += item_cfg["val"]
        elif item_cfg["type"] == "heal_both":
            hp_change += item_cfg["val"]
            mp_change += item_cfg["val"]
        elif item_cfg["type"] == "dmg":
            dmg_dealt = item_cfg["val"]

    # 2. 招式技能判定
    elif action["type"] in ["attack", "skill"]:
        raw_dmg = calculate_base_dmg(state[f"{actor_state_prefix}_class"], state[f"{actor_state_prefix}_weapon"],
                                     action)
        effectiveness = score / 5.0
        target_armor = state[f"{target_state_prefix}_armor"]
        def_rate = target_armor["def_rate"] if state[f"{actor_state_prefix}_weapon"]["type"] == "phys" else 0
        dmg_dealt = raw_dmg * effectiveness * (1 - def_rate)

        # 👈 【新玩法机制】：根据多技能配置名在代码中触发硬核特技数值
        action_name = action.get("name", "")

        # 特技 A：生命汲取 —— 造成的魔法伤害的 50% 直接转化为自身 HP 汲取
        if "生命汲取" in action_name:
            hp_change += int(dmg_dealt * 0.5)

        # 特技 B：圣盾庇护 —— 基础防御动作，并在护盾下获得 20 点生命圣光苏醒
        elif "圣盾庇护" in action_name and score > 0:
            hp_change += 20

    return hp_change, mp_change, int(dmg_dealt)


def judge_node(state: GameState, llm: ChatOpenAI, config: RunnableConfig):
    """裁判节点：LLM专注于文本战报和状态施加评分，支持动态 PvP 与 PvE 视角转换"""

    # 1. 动态解析红蓝双方的决斗者名字，让大模型输出个性化对决战报
    p1_profile = MOCK_PLAYER_DB.get(state["player_id"], {})
    p1_name = p1_profile.get("name", "红方决斗者")

    if state.get("game_mode") == "PvE":
        p2_name = "AI(电脑对手)"
    else:
        p2_profile = MOCK_PLAYER_DB.get(state.get("p2_id"), {})
        p2_name = p2_profile.get("name", "蓝方决斗者")

    sys_prompt = f"""
    你是RPG决斗裁判。你需要根据当前的战场环境、双方决斗者的职业面板、装备、武器以及各自做出的招式动作，推演出当前回合的结果，并给双方动作打出一个【效果分(0-10)】。
    - 10分：完美克制、看穿破绽、致命暴击（伤害翻倍）。
    - 5分：正常命中，平稳发挥（正常伤害）。
    - 0分：被完全闪避、被护甲盾牌完全化解、打偏或处于【眩晕】无法动弹（无伤害）。

    【异常状态与战术判定】：
    1. 异常状态（如 中毒、眩晕、烧伤、冻结、衰弱等）：当且仅当效果评分 > 4 时，技能附带的异常状态才能真正生效并写在 status 字段里返回（若无状态或抵抗了，返回'正常'）。
    2. 如果上一回合处于【眩晕】或【冻结】状态，本回合将无法行动，其动作评分应判定为 0 分。
    3. 防御动作一律评为 5 分，但能显著降低敌方本次物理/魔法攻击动作的效果分。

    环境：{{env}}
    【红方(P1)】 名字:{p1_name} | HP:{{p_hp}} | 状态:{{p_status}} | 属性:力{{p_str}}/敏{{p_agi}}/智{{p_int}} | 武器:{{p_w}} | 护甲:{{p_a}} | 动作:{{p_act}}
    【蓝方(P2)】 名字:{p2_name} | HP:{{a_hp}} | 状态:{{a_status}} | 属性:力{{a_str}}/敏{{a_agi}}/智{{a_int}} | 武器:{{a_w}} | 护甲:{{a_a}} | 动作:{{a_act}}
    """

    prompt = ChatPromptTemplate.from_messages([("system", sys_prompt)])
    chain = prompt | llm.with_structured_output(JudgeResult)

    try:
        res = chain.invoke({
            "env": state["environment"],
            "p_hp": state["player_hp"], "p_status": state["player_status"],
            "p_str": state["player_class"]["str"], "p_agi": state["player_class"]["agi"],
            "p_int": state["player_class"]["int"],
            "p_w": state["player_weapon"]["name"], "p_a": state["player_armor"]["name"],
            "p_act": state["player_action"]["name"],
            "a_hp": state["ai_hp"], "a_status": state["ai_status"],
            "a_str": state["ai_class"]["str"], "a_agi": state["ai_class"]["agi"], "a_int": state["ai_class"]["int"],
            "a_w": state["ai_weapon"]["name"], "a_a": state["ai_armor"]["name"], "a_act": state["ai_action"]["name"]
        }, config=config)
    except Exception as e:
        res = JudgeResult(
            combat_narration="双方激烈交锋，战场烟尘四起，战局退回常规拉锯战。",
            player_result=CombatResult(effectiveness_score=5, status="正常"),
            ai_result=CombatResult(effectiveness_score=5, status="正常")
        )

    item_lookup = {v["name"]: v for v in ITEMS.values()}

    # 代码双轨数值结算
    p_self_hp, p_self_mp, p_dmg_to_ai = process_action_effects(
        "player", "ai", state["player_action"], res.player_result.effectiveness_score, state, item_lookup, ARMORS
    )

    a_self_hp, a_self_mp, a_dmg_to_p = process_action_effects(
        "ai", "player", state["ai_action"], res.ai_result.effectiveness_score, state, item_lookup, ARMORS
    )

    # 持续掉血机制
    status_dmg_coeff = GLOBAL_CONFIGS.get("status_dmg_per_turn", 6)
    p_status_dmg = -status_dmg_coeff if state["player_status"] in ["中毒", "烧伤"] else 0
    a_status_dmg = -status_dmg_coeff if state["ai_status"] in ["中毒", "烧伤"] else 0

    new_p_hp = max(0, min(state["player_hp"] + p_self_hp - a_dmg_to_p + p_status_dmg,
                          state["player_class"]["hp"] + state["player_armor"]["hp_bonus"]))
    new_p_mp = max(0, state["player_mp"] + p_self_mp)

    new_a_hp = max(0, min(state["ai_hp"] + a_self_hp - p_dmg_to_ai + a_status_dmg,
                          state["ai_class"]["hp"] + state["ai_armor"]["hp_bonus"]))
    new_a_mp = max(0, state["ai_mp"] + a_self_mp)

    # 外部 Mock 数据库背包消耗扣减
    new_p_items = state["player_item_count"]
    if state["player_action"]["type"] == "item":
        new_p_items -= 1
        item_name = state["player_item"]["name"]
        item_id = next((k for k, v in ITEMS.items() if v["name"] == item_name), None)
        if item_id and state["player_id"] in MOCK_PLAYER_DB:
            profile = MOCK_PLAYER_DB[state["player_id"]]
            profile["inventory"]["items"][item_id] = max(0, profile["inventory"]["items"].get(item_id, 0) - 1)

    new_a_items = state["ai_item_count"] - 1 if state["ai_action"]["type"] == "item" else state["ai_item_count"]

    combat_log = (
        f"⚙️ 第 {state['turn_count']} 回合推演 ⚙️\n"
        f"📖 战报: {res.combat_narration}\n"
        f"⚔️ {p1_name}判定：效果 {res.player_result.effectiveness_score}/10 | 伤害 {p_dmg_to_ai} | 状态：{res.player_result.status}\n"
        f"⚔️ {p2_name}判定：效果 {res.ai_result.effectiveness_score}/10 | 伤害 {a_dmg_to_p} | 状态：{res.ai_result.status}\n"
        f"----------------------------------------\n"
        f"❤️ [{p1_name}] HP {new_p_hp} | MP {new_p_mp}\n"
        f"💀 [{p2_name}] HP {new_a_hp} | MP {new_a_mp}"
    )

    return {
        "player_hp": new_p_hp, "player_mp": new_p_mp, "player_status": res.player_result.status,
        "player_item_count": new_p_items,
        "ai_hp": new_a_hp, "ai_mp": new_a_mp, "ai_status": res.ai_result.status, "ai_item_count": new_a_items,
        "messages": [AIMessage(content=combat_log)],
        "turn_count": state["turn_count"] + 1
    }


def settlement_node(state: GameState, llm: ChatOpenAI, config: RunnableConfig):
    """结算：联机模式下暂不掉落战利品和金币"""
    is_pvp = state.get("game_mode") == "PvP"

    if state["player_hp"] <= 0 and state["ai_hp"] <= 0:
        return {"messages": [AIMessage(content="=== 📊 最终结算 ===\n💀 惨烈对决，同归于尽！")]}
    elif state["player_hp"] <= 0:
        winner = state.get("p2_id", "AI") if is_pvp else "强敌"
        return {"messages": [AIMessage(content=f"=== 📊 最终结算 ===\n❌ 战败。{winner} 赢得了胜利。")]}
    else:
        log_msg = "=== 📊 最终结算 ===\n🏆 获得胜利！"

        # 仅 PvE 模式发放资产与物资掉落奖励
        if not is_pvp:
            player_id = state["player_id"]
            if player_id in MOCK_PLAYER_DB:
                profile = MOCK_PLAYER_DB[player_id]
                min_gold = GLOBAL_CONFIGS.get("min_gold_drop", 30)
                max_gold = GLOBAL_CONFIGS.get("max_gold_drop", 60)
                gold_drop = random.randint(min_gold, max_gold)
                profile["gold"] += gold_drop

                loot_type = random.choice(["weapon", "armor", "item"])
                loot_msg = ""
                if loot_type == "weapon":
                    avail = [k for k in WEAPONS.keys() if k not in profile["inventory"]["weapons"]]
                    if avail:
                        new_id = random.choice(avail)
                        profile["inventory"]["weapons"].append(new_id)
                        loot_msg = f"全新武器【{WEAPONS[new_id]['name']}】"
                    else:
                        loot_type = "item"
                if loot_type == "armor":
                    avail = [k for k in ARMORS.keys() if k not in profile["inventory"]["armors"]]
                    if avail:
                        new_id = random.choice(avail)
                        profile["inventory"]["armors"].append(new_id)
                        loot_msg = f"武装防具【{ARMORS[new_id]['name']}】"
                    else:
                        loot_type = "item"
                if loot_type == "item":
                    new_id = random.choice(list(ITEMS.keys()))
                    profile["inventory"]["items"][new_id] = profile["inventory"]["items"].get(new_id, 0) + 1
                    loot_msg = f"炼金道具【{ITEMS[new_id]['name']}】 x1"

                log_msg += f"\n💰 斩获战利金：+ {gold_drop} 金币！\n🎁 额外搜刮获得：{loot_msg}"

        else:
            log_msg += "\n⚔️ (PvP联机决斗已结束，荣誉即是最高奖赏)"

        return {"messages": [AIMessage(content=log_msg)]}
