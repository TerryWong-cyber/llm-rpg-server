# craft.py
import os
import uuid
from typing import TypedDict, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

# 导入 Langfuse 追踪依赖
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

# 导入装备字典 registry
from agent_nodes import WEAPONS, ARMORS, ITEMS

load_dotenv()
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "whatever")
# 如果你使用本地/代理地址，请保留下面这行；如果是官方 API，请注释掉
os.environ["OPENAI_API_BASE"] = os.getenv("OPENAI_API_BASE", "http://127.0.0.1:5057/v1")

langfuse = get_client()
langfuse_handler = CallbackHandler()

# 初始化大模型与追踪客户端
llm = ChatOpenAI(model="qwen3-vl-8b-instruct", temperature=0.7)

# ==========================================
# 新增：全局合成公式缓存表 (Requirement 4 & 5)
# 格式: { ("id1", "id2"): result_dict }
# ==========================================
CRAFT_RECIPES = {}


def get_recipe_key(id1: str, id2: str) -> tuple:
    """对素材 ID 排序后生成元组，保证 A+B 和 B+A 对应同一个公式"""
    return tuple(sorted([id1, id2]))


# ==========================================
# Schema 定义 (Requirement 1)
# ==========================================
class CraftResult(BaseModel):
    name: str = Field(description="熔炼出的新物品名称")
    desc: str = Field(description="新物品奇幻、酷炫的文字描述说明")
    value: int = Field(description="系统对新物品进行估价（金币数值）")
    item_type: Literal["weapon", "armor", "item", "material"] = Field(
        description="物品的类型：武器、防具、消耗道具或基础材料")
    combat_stat: int = Field(
        description="数值属性：若为武器代表基础伤害(10-40)，防具代表生命加成(10-50)，道具代表恢复量(20-100)，材料填0")


class CraftState(TypedDict):
    player_id: str
    item1_type: str
    item1_id: str
    item2_type: str
    item2_id: str
    profile: Optional[Dict[str, Any]]
    info1: Optional[Dict[str, Any]]
    info2: Optional[Dict[str, Any]]
    result: Optional[Dict[str, Any]]
    error: Optional[str]


# Node 1: 数据校验与玩家档案加载
def validate_and_load_node(state: CraftState):
    player_id = state["player_id"]
    from server import MOCK_PLAYER_DB
    profile = MOCK_PLAYER_DB.get(player_id)
    if not profile:
        return {"error": "玩家存档不存在"}

    inv = profile.get("inventory", {})
    item1_type, item1_id = state["item1_type"], state["item1_id"]
    item2_type, item2_id = state["item2_type"], state["item2_id"]

    def get_item_info(itype, iid):
        if itype == "weapon" and iid in inv.get("weapons", []): return WEAPONS.get(iid)
        if itype == "armor" and iid in inv.get("armors", []): return ARMORS.get(iid)
        if itype == "item" and inv.get("items", {}).get(iid, 0) > 0: return ITEMS.get(iid)
        if itype == "material":
            materials_dict = inv.get("materials", {})
            mat = materials_dict.get(iid)
            if mat is not None:
                if isinstance(mat, dict) and mat.get("count", 0) > 0:
                    return mat
                elif isinstance(mat, int) and mat > 0:
                    try:
                        from config_loader import RESOURCES
                        res_meta = RESOURCES.get(iid, {})
                    except ImportError:
                        res_meta = {}
                    emoji_str = res_meta.get("emoji", "")
                    display_name = f"{emoji_str} {res_meta['name']}" if emoji_str and "name" in res_meta else res_meta.get(
                        "name", iid)
                    return {"name": display_name, "desc": res_meta.get("desc", ""), "value": res_meta.get("value", 5),
                            "count": mat}
        return None

    info1 = get_item_info(item1_type, item1_id)
    info2 = get_item_info(item2_type, item2_id)

    if not info1 or not info2: return {"error": "所选物品不存在或库存不足"}
    if item1_type == item2_type and item1_id == item2_id:
        if item1_type in ["weapon", "armor"] and inv.get(item1_type + "s", []).count(item1_id) < 2:
            return {"error": "该装备数量不足"}
        elif item1_type == "item" and inv.get("items", {}).get(item1_id, 0) < 2:
            return {"error": "该道具数量不足"}
        elif item1_type == "material" and (
                inv.get("materials", {}).get(item1_id, {}).get("count", 0) if isinstance(
                    inv.get("materials", {}).get(item1_id),
                    dict) else inv.get("materials",
                                       {}).get(item1_id,
                                               0)) < 2:
            return {"error": "该素材数量不足"}

    if item1_type == "weapon" and len(inv.get("weapons", [])) <= 1: return {"error": "必须保留至少一把武器"}
    if item2_type == "weapon" and len(inv.get("weapons", [])) <= 1: return {"error": "必须保留至少一把武器"}
    if item1_type == "armor" and len(inv.get("armors", [])) <= 1: return {"error": "必须保留至少一件防具"}
    if item2_type == "armor" and len(inv.get("armors", [])) <= 1: return {"error": "必须保留至少一件防具"}

    return {"profile": profile, "info1": info1, "info2": info2}


# Node 2: 扣减物品
def deduct_items_node(state: CraftState):
    if state.get("error"): return {}
    profile = state["profile"]
    inv = profile["inventory"]

    def deduct(itype, iid):
        if itype == "weapon":
            inv["weapons"].remove(iid)
        elif itype == "armor":
            inv["armors"].remove(iid)
        elif itype == "item":
            inv["items"][iid] -= 1
        elif itype == "material":
            mat = inv["materials"][iid]
            if isinstance(mat, dict):
                mat["count"] -= 1
                if mat["count"] == 0: del inv["materials"][iid]
            else:
                inv["materials"][iid] -= 1
                if inv["materials"][iid] <= 0: del inv["materials"][iid]

    deduct(state["item1_type"], state["item1_id"])
    deduct(state["item2_type"], state["item2_id"])
    return {"profile": profile}


# Node 3: LLM 推理计算 (Requirement 5: 若有缓存则直接使用)
def llm_craft_node(state: CraftState, config: Optional[dict] = None):
    if state.get("error"): return {}

    item1_id, item2_id = state["item1_id"], state["item2_id"]
    recipe_key = get_recipe_key(item1_id, item2_id)

    # 【需求 5】：检测缓存，如果已合成过则直接命中
    if recipe_key in CRAFT_RECIPES:
        print(f"🔄 命中合成公式缓存：{state['info1']['name']} + {state['info2']['name']}")
        cached_result = CRAFT_RECIPES[recipe_key].copy()
        # 即使是旧配方，每次造出实体也要发一个新 UUID（如果是作为唯一装备的话）
        # 为了防重复冲突，我们给返回结果附带上本次的新ID
        cached_result["id"] = f"craft_{uuid.uuid4().hex[:8]}"
        return {"result": cached_result}

    info1, info2 = state["info1"], state["info2"]

    sys_prompt = """你是一位作风严谨、讲求科学的材料学家、冶金专家与炼金大师。
        1. 写实与合理：结合原材料推演合成结果。
        2. 物品分类：必须严格选择生成的物品类型(item_type)：
           - 具有攻击性/利刃/火器的输出物，设为 'weapon'，combat_stat 代表基础物理/魔法伤害(10-40)。
           - 具有防御/护甲特性的衣物/盾牌，设为 'armor'，combat_stat 代表提供的额外生命值(10-50)。
           - 消耗类药水/炸弹/卷轴，设为 'item'，combat_stat 代表其恢复生命量或造成的一次性伤害(20-100)。
           - 无法直接穿戴或使用的中间矿石/锭料/皮革，设为 'material'，combat_stat 填 0。
        3. 描述：简短且极具奇幻游戏色彩。"""

    user_prompt = f"""【原料1】名称:{info1['name']} 描述:{info1.get('desc')} 价值:{info1.get('value')} \n【原料2】名称:{info2['name']} 描述:{info2.get('desc')} 价值:{info2.get('value')}"""

    prompt = ChatPromptTemplate.from_messages([("system", sys_prompt), ("user", user_prompt)])
    chain = prompt | llm.with_structured_output(CraftResult)
    res = chain.invoke({}, config=config)

    crafted_item = {
        "id": f"craft_{uuid.uuid4().hex[:8]}",
        "name": res.name,
        "desc": res.desc,
        "value": res.value,
        "type": res.item_type,
        "combat_stat": res.combat_stat
    }
    return {"result": crafted_item}


# Node 4: 物品画图 (Requirement 3: 接入文生图节点)
def image_gen_node(state: CraftState):
    if state.get("error"): return {}
    result = state["result"]

    recipe_key = get_recipe_key(state["item1_id"], state["item2_id"])

    # 若之前缓存过且包含图片，无需重新生成
    if "image_url" in result and result["image_url"]:
        return {}

    try:
        from openai import OpenAI
        # 这里调用 OpenAI 官方的 DALL-E (或其他兼容接口的文生图)
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_API_BASE"))
        img_prompt = f"A single icon of a fantasy RPG game item named '{result['name']}'. Description: {result['desc']}. High quality 2D digital art, isolated on a solid dark background, suitable for inventory."

        response = client.images.generate(
            model="dall-e-3",  # 如果你用其他模型请替换
            prompt=img_prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
    except Exception as e:
        print(f"⚠️ 图像生成失败（将使用占位图）: {e}")
        # 如果没有配置 key 或请求失败，给一张写着名字的占位图防崩溃
        image_url = f"https://placehold.co/256x256/2a2a2a/f0f0f0?text={result['name']}"

    result["image_url"] = image_url

    # 【需求 4】：存储一条记录在内存表内，包含合成的图片和属性
    if recipe_key not in CRAFT_RECIPES:
        CRAFT_RECIPES[recipe_key] = result.copy()

    return {"result": result}


# Node 5: 数据库入库与全局字典注册 (Requirement 2)
def save_profile_node(state: CraftState):
    if state.get("error"): return {}
    profile = state["profile"]
    result = state["result"]
    inv = profile["inventory"]
    item_type = result["type"]
    item_id = result["id"]

    # 局部导入全局资产字典，防止循环依赖
    from config_loader import WEAPONS, ARMORS, ITEMS, RESOURCES
    from server import MOCK_PLAYER_DB

    # 根据大模型定义的类型，自动填充对应背包类别并进行全局注册
    if item_type == "weapon":
        if item_id not in WEAPONS:
            WEAPONS[item_id] = {
                "id": item_id, "name": result["name"], "base_dmg": result.get("combat_stat", 15),
                "range": "近战", "type": "phys", "value": result["value"], "desc": result["desc"],
                "image_url": result.get("image_url", ""),
                "skills": [{"id": "1", "name": "造物打击", "cost": 10, "multiplier": 1.2, "desc": "自制专属战技"}]
            }
        inv["weapons"].append(item_id)

    elif item_type == "armor":
        if item_id not in ARMORS:
            ARMORS[item_id] = {
                "id": item_id, "name": result["name"], "hp_bonus": result.get("combat_stat", 20),
                "def_rate": 0.15, "value": result["value"], "desc": result["desc"],
                "image_url": result.get("image_url", "")
            }
        inv["armors"].append(item_id)

    elif item_type == "item":
        if item_id not in ITEMS:
            ITEMS[item_id] = {
                "id": item_id, "name": result["name"], "type": "heal_hp",
                "val": result.get("combat_stat", 30), "value": result["value"], "desc": result["desc"],
                "image_url": result.get("image_url", "")
            }
        inv["items"][item_id] = inv["items"].get(item_id, 0) + 1

    else:  # material
        if item_id not in RESOURCES:
            RESOURCES[item_id] = {
                "id": item_id, "name": result["name"], "emoji": "✨",
                "desc": result["desc"], "value": result["value"],
                "image_url": result.get("image_url", "")
            }
        if "materials" not in inv: inv["materials"] = {}
        if item_id not in inv["materials"]:
            inv["materials"][item_id] = {"id": item_id, "name": result["name"], "desc": result["desc"],
                                         "value": result["value"], "type": "material", "count": 1,
                                         "image_url": result.get("image_url", "")}
        else:
            inv["materials"][item_id]["count"] += 1

    # 回写至全局内存数据库
    MOCK_PLAYER_DB[state["player_id"]] = profile
    return {"profile": profile}


# ==========================================
# 组装炼金 StateGraph 工作流
# ==========================================
workflow = StateGraph(CraftState)

workflow.add_node("Validate", validate_and_load_node)
workflow.add_node("Deduct", deduct_items_node)
workflow.add_node("LLMCraft", lambda state, config: llm_craft_node(state, config))
workflow.add_node("ImageGen", image_gen_node)  # 👈 新增图片生成节点
workflow.add_node("Save", save_profile_node)

workflow.set_entry_point("Validate")


def route_after_validation(state: CraftState):
    if state.get("error"): return END
    return "Deduct"


workflow.add_conditional_edges("Validate", route_after_validation, {END: END, "Deduct": "Deduct"})
workflow.add_edge("Deduct", "LLMCraft")
workflow.add_edge("LLMCraft", "ImageGen")  # 👈 LLM推演完之后进行配图
workflow.add_edge("ImageGen", "Save")  # 👈 配图完毕后保存
workflow.add_edge("Save", END)

memory = MemorySaver()
craft_app = workflow.compile(checkpointer=memory)


# ==========================================
# 封装运行函数
# ==========================================
def run_crafting(player_id: str, item1_type: str, item1_id: str, item2_type: str, item2_id: str) -> dict:
    session_id = f"craft_session_{uuid.uuid4().hex[:10]}"
    config = {"configurable": {"thread_id": session_id}, "callbacks": [langfuse_handler],
              "run_name": "RPG_Item_Crafting"}

    initial_state = {
        "player_id": player_id,
        "item1_type": item1_type, "item1_id": item1_id,
        "item2_type": item2_type, "item2_id": item2_id
    }

    print(f"\n[炼金工坊] 开始为玩家 {player_id} 熔炼物品...")
    with langfuse.start_as_current_observation(as_type="span", name="Epic_Crafting_Workflow") as trace:
        with propagate_attributes(session_id=session_id):
            final_state = craft_app.invoke(initial_state, config=config)

    try:
        langfuse.flush()
    except Exception as e:
        pass

    if final_state.get("error"):
        return {"success": False, "error": final_state["error"]}
    return {"success": True, "result": final_state.get("result")}
