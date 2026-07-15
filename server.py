# server.py
import uuid
import random
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

# 从 fight 模块中获取配置的 app, langfuse 以及相关组件
from fight import fight_app, langfuse_handler, langfuse, llm
from craft import run_crafting
from langfuse import propagate_attributes

app_api = FastAPI(title="硬核 RPG 对决 API (联机同步版)", version="2.0.0")

app_api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# server.py (新增与修改部分)

# 1. 替换原来的引用，引入 config_loader 和 map_system
from config_loader import CHARACTERS, WEAPONS, ARMORS, ITEMS, RESOURCES, TERRAINS
from map_system import generate_5x5_map, gather_from_cell
from agent_nodes import MOCK_PLAYER_DB, create_player_profile
from npc_dialogue import NPCDialogueService
from npc_interaction import NPCInteractionService
from npc_seed import seed_demo_npcs
from world_repository import InMemoryWorldRepository


# The service is deliberately separate from FastAPI and combat.  Replace this
# repository with a database adapter later without changing route handlers.
WORLD_REPOSITORY = InMemoryWorldRepository()
seed_demo_npcs(WORLD_REPOSITORY)
NPC_INTERACTIONS = NPCInteractionService(WORLD_REPOSITORY, NPCDialogueService(llm))


# 2. 修改 create_player_profile (在 agent_nodes.py 中) 给玩家新增 materials 字典和 map 缓存
# (请去 agent_nodes.py 找到 create_player_profile 函数，把 "inventory" 改为包含 "materials" 的结构)
# "inventory": { "weapons": ["1"], "armors": ["0"], "items": {"1": 3}, "materials": {} },
# "current_map": None


# ==========================================
# WebSocket 联机房间实体与缓冲区设计
# ==========================================
class GameRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.p1_id = None
        self.p2_id = None
        self.mode = "WAITING"  # 默认为人机模式，有真人连入时自动升级为 PvP
        self.is_started = False
        self.thread_id = f"room_{room_id}_{uuid.uuid4().hex[:6]}"

        self.p1_ws: Optional[WebSocket] = None
        self.p2_ws: Optional[WebSocket] = None

        # 双端锁存缓冲区（保障并发状态下的决斗指令一致性）
        self.p1_prep = None
        self.p2_prep = None
        self.p1_act = None
        self.p2_act = None

        # Optional link to a world NPC.  Regular PvP/PvE rooms do not use it.
        self.npc_id: Optional[str] = None
        self.npc_trigger_id: Optional[str] = None
        self.npc_outcome_recorded = False

    async def broadcast(self, data: dict):
        """向房间内全体在线活跃玩家广播消息"""
        for ws in [self.p1_ws, self.p2_ws]:
            if ws:
                try:
                    await ws.send_json(data)
                except Exception:
                    pass


ACTIVE_ROOMS: Dict[str, GameRoom] = {}


def _model_dict(model) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


# ==========================================
# Pydantic 交互协议模型
# ==========================================
class CreateCharacterRequest(BaseModel):
    name: str
    character_id: str


class StartRequest(BaseModel):
    player_id: str


class TradeRequest(BaseModel):
    player_id: str
    thread_id: Optional[str] = None  # 传入战斗 ID 时，交易完成后同步返回最新战斗快照
    item_type: str  # "weapon" | "armor" | "item"
    item_id: str


class NPCDialogueRequest(BaseModel):
    player_id: str
    message: str = Field(min_length=1, max_length=500)


class NPCCombatStartRequest(BaseModel):
    player_id: str
    trigger_id: str


# ==========================================
# 辅助快照函数：合并 LangGraph 战斗状态与 Mock DB 资产
# ==========================================
def get_ws_snapshot(room: GameRoom) -> Dict[str, Any]:
    config = {"configurable": {"thread_id": room.thread_id}}
    state_snapshot = fight_app.get_state(config)
    values = state_snapshot.values if state_snapshot.values else {}
    next_node = state_snapshot.next[0] if state_snapshot.next else None

    combat_log = values["messages"][-1].content if values.get("messages") else ""
    game_over = next_node is None or "Settlement" in str(next_node)

    # 查库提取双方玩家最新的金币和背包
    p1_profile = MOCK_PLAYER_DB.get(room.p1_id, {})
    p2_profile = MOCK_PLAYER_DB.get(room.p2_id, {}) if room.mode == "PvP" else {}
    npc_enemy = None
    if room.npc_id:
        try:
            npc_view = NPC_INTERACTIONS.get_npc_view(room.npc_id, room.p1_id)
            npc_enemy = dict(npc_view["npc"])
            npc_enemy["relationship"] = _model_dict(npc_view["relationship"])
        except KeyError:
            # A missing NPC must not prevent an already-running combat room from
            # returning its deterministic battle state.
            npc_enemy = None

    return {
        "room_id": room.room_id,
        "thread_id": room.thread_id,
        "next_node": next_node,
        "game_over": game_over,
        "state": {
            "environment": values.get("environment"),
            "turn_count": values.get("turn_count", 1),
            "game_mode": room.mode,
            "p1_id": room.p1_id,
            "p2_id": room.p2_id,

            # --- 1P 真人金币与背囊 (来自外部 MOCK 数据库) ---
            "player_gold": p1_profile.get("gold", 0),
            "player_inventory": p1_profile.get("inventory", {"weapons": ["1"], "armors": ["0"], "items": {"1": 3}}),
            # --- 1P 战斗运行时属性 ---
            "player_class": values.get("player_class"),
            "player_weapon": values.get("player_weapon"),
            "player_armor": values.get("player_armor"),
            "player_item": values.get("player_item"),
            "player_item_count": values.get("player_item_count", 0),
            "player_hp": values.get("player_hp", 0),
            "player_mp": values.get("player_mp", 0),
            "player_status": values.get("player_status", "正常"),

            # --- 2P/AI 玩家金币与背囊 ---
            "ai_gold": p2_profile.get("gold", 0),
            "ai_inventory": p2_profile.get("inventory", {"weapons": ["1"], "armors": ["0"], "items": {"1": 3}}),
            # --- 2P/AI 战斗运行时属性 ---
            "ai_class": values.get("ai_class"),
            "ai_weapon": values.get("ai_weapon"),
            "ai_armor": values.get("ai_armor"),
            "ai_item": values.get("ai_item"),
            "ai_item_count": values.get("ai_item_count", 0),
            "ai_hp": values.get("ai_hp", 0),
            "ai_mp": values.get("ai_mp", 0),
            "ai_status": values.get("ai_status", "正常"),
        },
        "combat_log": combat_log,
        "npc_enemy": npc_enemy,
    }


# ==========================================
# 路由接口（创角与房间管理）
# ==========================================

@app_api.get("/api/game/meta")
def get_game_meta():
    return {
        "characters": CHARACTERS,
        "weapons": WEAPONS,
        "armors": ARMORS,
        "items": ITEMS,
        "resources": RESOURCES
    }


class GatherRequest(BaseModel):
    player_id: str
    cell_id: int


# ==================== 新增：地图探索 API ====================
@app_api.post("/api/map/enter")
def enter_map(req: StartRequest):
    """进入探索模式，获取或刷新地图"""
    profile = MOCK_PLAYER_DB.get(req.player_id)
    if not profile: raise HTTPException(404, "角色不存在")

    # 如果玩家没有地图缓存，或者想主动刷新，则生成新地图
    if not profile.get("current_map"):
        profile["current_map"] = generate_5x5_map()

    return {
        "map_grid": profile["current_map"],
        "terrains_meta": TERRAINS,
        "resources_meta": RESOURCES,
        "inventory_materials": profile["inventory"].get("materials", {})
    }


@app_api.post("/api/map/gather")
def map_gather(req: GatherRequest):
    """点击格子进行采集"""
    profile = MOCK_PLAYER_DB.get(req.player_id)
    if not profile or not profile.get("current_map"):
        raise HTTPException(404, "无效的探索状态")

    map_grid = profile["current_map"]
    if req.cell_id < 0 or req.cell_id >= len(map_grid):
        raise HTTPException(400, "非法的坐标")

    cell = map_grid[req.cell_id]
    if cell["is_gathered"]:
        raise HTTPException(400, "该地块资源已枯竭")

    # 执行采集计算
    loot = gather_from_cell(cell["terrain_id"])
    cell["is_gathered"] = True  # 标记为枯竭

    # 将物资加入玩家背包
    materials_inv = profile["inventory"].setdefault("materials", {})
    for res_id, qty in loot.items():
        materials_inv[res_id] = materials_inv.get(res_id, 0) + qty

    # 组装返回给前端的 UI 提示信息
    loot_msgs = [f"{RESOURCES[k]['emoji']} {RESOURCES[k]['name']} x{v}" for k, v in loot.items()]
    msg = "、".join(loot_msgs) if loot_msgs else "什么也没找到..."
    terrain_name = TERRAINS.get(cell["terrain_id"], {}).get("name", "未知地带")
    NPC_INTERACTIONS.record_player_event(
        req.player_id,
        f"你在{terrain_name}完成了一次采集：{msg}",
        tags=["exploration", "gather", f"terrain:{cell['terrain_id']}"],
        importance=2 if loot else 1,
    )

    return {
        "status": "success",
        "msg": msg,
        "loot": loot,
        "cell_id": req.cell_id,
        "inventory_materials": materials_inv
    }


@app_api.post("/api/game/character/create")
def create_character(req: CreateCharacterRequest):
    """建立新角色，并在 Mock 数据库里分配 100 初始金币与初始背包"""
    if req.character_id not in CHARACTERS:
        raise HTTPException(status_code=400, detail="选定的基础职业不存在")

    player_id = f"char_{uuid.uuid4().hex[:8]}"
    profile = create_player_profile(player_id, req.name, req.character_id)
    return {
        "status": "success",
        "player_id": player_id,
        "profile": profile
    }


# ==========================================
# 世界 NPC：资料、双向记忆、对话与遭遇战
# ==========================================
@app_api.post("/api/world/npcs/seed-demo")
def seed_world_npcs():
    """Idempotently install three sample NPCs for local development/demo."""
    npcs = seed_demo_npcs(WORLD_REPOSITORY)
    return {"status": "success", "npcs": [NPC_INTERACTIONS.public_npc(npc) for npc in npcs]}


@app_api.get("/api/world/npcs")
def list_world_npcs(terrain_id: Optional[str] = None, cell_id: Optional[int] = None):
    """Use terrain_id/cell_id from the exploration map to render nearby NPCs."""
    return {"npcs": NPC_INTERACTIONS.list_npcs(terrain_id=terrain_id, cell_id=cell_id)}


@app_api.get("/api/world/npcs/{npc_id}")
def get_world_npc(npc_id: str, player_id: Optional[str] = None):
    try:
        if player_id:
            if player_id not in MOCK_PLAYER_DB:
                raise HTTPException(status_code=404, detail="未找到角色档案")
            return NPC_INTERACTIONS.get_npc_view(npc_id, player_id)
        return {"npc": NPC_INTERACTIONS.public_npc(WORLD_REPOSITORY.get_npc(npc_id))}
    except KeyError:
        raise HTTPException(status_code=404, detail="NPC 不存在")


@app_api.post("/api/world/npcs/{npc_id}/dialogue")
def talk_to_world_npc(npc_id: str, req: NPCDialogueRequest):
    if req.player_id not in MOCK_PLAYER_DB:
        raise HTTPException(status_code=404, detail="未找到角色档案")
    try:
        return NPC_INTERACTIONS.interact(npc_id, req.player_id, req.message)
    except KeyError:
        raise HTTPException(status_code=404, detail="NPC 不存在")


@app_api.get("/api/world/npcs/{npc_id}/memories")
def get_npc_memories(npc_id: str, player_id: str):
    if player_id not in MOCK_PLAYER_DB:
        raise HTTPException(status_code=404, detail="未找到角色档案")
    try:
        return {"npc_id": npc_id, "memories": NPC_INTERACTIONS.npc_memories(npc_id, player_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="NPC 不存在")


@app_api.get("/api/world/players/{player_id}/memories")
def get_player_memories(player_id: str):
    if player_id not in MOCK_PLAYER_DB:
        raise HTTPException(status_code=404, detail="未找到角色档案")
    return {"player_id": player_id, "memories": NPC_INTERACTIONS.player_memories(player_id)}


@app_api.get("/api/world/facts")
def get_world_facts():
    """Public consequences that future NPCs, quests, and news systems may consume."""
    return {"facts": NPC_INTERACTIONS.world_facts()}


def _make_npc_combat_room(player_id: str, npc_id: str, trigger_id: str) -> GameRoom:
    """Adapter from a validated NPC trigger to the existing PvE LangGraph flow."""
    npc, combat = NPC_INTERACTIONS.start_combat(npc_id, player_id, trigger_id)
    if combat.character_id not in CHARACTERS or combat.weapon_id not in WEAPONS or combat.armor_id not in ARMORS:
        raise ValueError("NPC 战斗配置引用了不存在的职业或装备")
    if combat.item_id and combat.item_id not in ITEMS:
        raise ValueError("NPC 战斗配置引用了不存在的消耗品")

    while True:
        room_id = f"{random.randint(1000, 9999)}"
        if room_id not in ACTIVE_ROOMS:
            break

    room = GameRoom(room_id)
    room.p1_id = player_id
    room.p2_id = f"NPC_{npc_id}"
    room.mode = "PvE"
    room.npc_id = npc_id
    room.npc_trigger_id = trigger_id
    room.is_started = True
    ACTIVE_ROOMS[room_id] = room

    config = {"configurable": {"thread_id": room.thread_id}}
    fight_app.invoke({
        "messages": [],
        "player_id": player_id,
        "p2_id": room.p2_id,
        "game_mode": "PvE",
    }, config=config)

    enemy_class = CHARACTERS[combat.character_id]
    enemy_armor = ARMORS[combat.armor_id]
    fight_app.update_state(config, {
        "environment": combat.arena,
        "ai_class": enemy_class,
        "ai_weapon": WEAPONS[combat.weapon_id],
        "ai_armor": enemy_armor,
        "ai_item": ITEMS.get(combat.item_id) if combat.item_id else None,
        "ai_item_count": combat.item_count,
        "ai_hp": enemy_class["hp"] + enemy_armor["hp_bonus"],
        "ai_mp": enemy_class["mp"],
        "ai_status": "正常",
    })
    return room


def _record_npc_combat_outcome(room: GameRoom) -> None:
    """Persist a battle consequence exactly once after the LangGraph reaches END."""
    if not room.npc_id or room.npc_outcome_recorded:
        return
    config = {"configurable": {"thread_id": room.thread_id}}
    values = fight_app.get_state(config).values
    player_hp = values.get("player_hp", 0)
    npc_hp = values.get("ai_hp", 0)
    if player_hp <= 0 and npc_hp <= 0:
        player_won = None
    else:
        player_won = player_hp > 0 and npc_hp <= 0
    NPC_INTERACTIONS.record_combat_outcome(room.npc_id, room.p1_id, player_won)
    room.npc_outcome_recorded = True


@app_api.post("/api/world/npcs/{npc_id}/combat/start")
def start_world_npc_combat(npc_id: str, req: NPCCombatStartRequest):
    if req.player_id not in MOCK_PLAYER_DB:
        raise HTTPException(status_code=404, detail="未找到角色档案")
    try:
        room = _make_npc_combat_room(req.player_id, npc_id, req.trigger_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="NPC 不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "status": "success",
        "room_id": room.room_id,
        "websocket_path": f"/ws/room/{room.room_id}/{req.player_id}",
        "snapshot": get_ws_snapshot(room),
    }


@app_api.post("/api/room/create")
def create_room(req: StartRequest):
    """【新加】：房主创建专属决斗房间"""
    if req.player_id not in MOCK_PLAYER_DB:
        raise HTTPException(status_code=404, detail="未找到角色档案")

    # 随机生成一个没有被占用的 4 位数字房间号
    while True:
        room_id = f"{random.randint(1000, 9999)}"
        if room_id not in ACTIVE_ROOMS:
            break

    room = GameRoom(room_id)
    room.p1_id = req.player_id
    ACTIVE_ROOMS[room_id] = room
    return {"room_id": room_id}


@app_api.post("/api/room/join")
def join_room(req: dict):
    """【新加】：对手加入已有房间并自动升级游戏为 PvP 模式"""
    room_id = str(req.get("room_id"))
    player_id = req.get("player_id")

    if room_id not in ACTIVE_ROOMS:
        raise HTTPException(status_code=404, detail="决斗房间无效或已解散")

    room = ACTIVE_ROOMS[room_id]
    if room.p2_id or room.is_started:
        raise HTTPException(status_code=400, detail="该房间已满员或已开战")

    room.p2_id = player_id
    room.mode = "PvP"
    return {"room_id": room_id, "mode": "PvP"}


@app_api.post("/api/room/add_ai")
async def room_add_ai(req: dict):  # 👈 【修复】：升级为 async 接口以支持广播
    room_id = str(req.get("room_id"))
    if room_id not in ACTIVE_ROOMS:
        raise HTTPException(status_code=404, detail="房间号不存在")

    room = ACTIVE_ROOMS[room_id]
    room.p2_id = "AI_BOT"
    room.mode = "PvE"  # 👈 房主手动确定人机战，状态转换为 PvE

    # 👈 【修复】：房主主动要求打人机，在这里直接启动战斗并进行通知
    if not room.is_started:
        room.is_started = True
        config = {"configurable": {"thread_id": room.thread_id}}
        fight_app.invoke({
            "messages": [],
            "player_id": room.p1_id,
            "p2_id": room.p2_id,
            "game_mode": room.mode
        }, config=config)
        await room.broadcast({"event": "game_start", "snapshot": get_ws_snapshot(room)})

    return {"room_id": room_id, "mode": "PvE"}


# ==========================================
# 接口：集市交易 (直连外部 DB，保障高频点击不会和状态机冲突)
# ==========================================

@app_api.post("/api/game/shop/buy")
def buy_item(req: TradeRequest):
    profile = MOCK_PLAYER_DB.get(req.player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="未找到对应的玩家存档")

    gold = profile["gold"]
    inventory = profile["inventory"]

    price = 0
    if req.item_type == "weapon":
        if req.item_id not in WEAPONS: raise HTTPException(400, "无效武器ID")
        if req.item_id in inventory["weapons"]: raise HTTPException(400, "已拥有此武器")
        price = WEAPONS[req.item_id]["value"]
    elif req.item_type == "armor":
        if req.item_id not in ARMORS: raise HTTPException(400, "无效防具ID")
        if req.item_id in inventory["armors"]: raise HTTPException(400, "已拥有此防具")
        price = ARMORS[req.item_id]["value"]
    elif req.item_type == "item":
        if req.item_id not in ITEMS: raise HTTPException(400, "无效道具ID")
        price = ITEMS[req.item_id]["value"]
    else:
        raise HTTPException(400, "未知商品大类")

    if gold < price: raise HTTPException(400, "金币不足，无法购买")

    profile["gold"] -= price
    if req.item_type == "weapon":
        profile["inventory"]["weapons"].append(req.item_id)
    elif req.item_type == "armor":
        profile["inventory"]["armors"].append(req.item_id)
    elif req.item_type == "item":
        profile["inventory"]["items"][req.item_id] = profile["inventory"]["items"].get(req.item_id, 0) + 1

    # 如果玩家在开战前大厅交易，附带传回匹配的战斗 snapshot 供页面即时刷新金币和背囊
    if req.thread_id:
        room = next((r for r in ACTIVE_ROOMS.values() if r.thread_id == req.thread_id), None)
        if room:
            return get_ws_snapshot(room)

    return {"status": "success", "profile": profile}


# server.py (新增所需的导包与 Pydantic 模型)
from langchain_core.prompts import ChatPromptTemplate


# 在 Pydantic 模型定义区（约第 53 行下方）新增合成交互模型：
class CraftRequest(BaseModel):
    player_id: str
    # 物品 1
    item1_type: str  # "weapon" | "armor" | "item" | "material"
    item1_id: str
    # 物品 2
    item2_type: str
    item2_id: str


class CraftResult(BaseModel):
    name: str = Field(description="合成出的全新素材/宝物名称（如：烈火淬毒钢核、奥术铁锭等）")
    desc: str = Field(description="物品的外观、特性与背景故事描述（50字内）")
    value: int = Field(description="该物品的评估价值（金币），通常大致为两件原物品价值之和，若极具创意可适当上浮")


# ==================== 新增：炼金合成接口 ====================
from craft import craft_app


@app_api.post("/api/game/craft")
def craft_item(req: CraftRequest):
    """通过 LangGraph 炼金工作流引擎熔炼物品"""

    # 1. 直接调用封装好的带有 Langfuse 追踪的运行函数
    try:
        response = run_crafting(
            player_id=req.player_id,
            item1_type=req.item1_type,
            item1_id=req.item1_id,
            item2_type=req.item2_type,
            item2_id=req.item2_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"炼金发生严重异常: {str(e)}")

    # 2. 校验合成规则（防裸奔、库存不足等业务拦截）
    if not response["success"]:
        raise HTTPException(status_code=400, detail=response["error"])

    # 3. 成功后，从 DB 拉取最新档案并返回给前端
    updated_profile = MOCK_PLAYER_DB.get(req.player_id)

    return {
        "status": "success",
        "result": response["result"],
        "profile": updated_profile
    }

# 👇 引入我们刚才在 craft.py 里加的字典
from craft import CRAFT_RECIPES

@app_api.get("/api/game/recipes")
def get_craft_recipes():
    """获取全服玩家共同探索出的炼金图鉴"""
    recipes_list = []
    for (id1, id2), result in CRAFT_RECIPES.items():
        recipes_list.append({
            "mat1_id": id1,
            "mat2_id": id2,
            "result": result
        })
    return {"status": "success", "recipes": recipes_list}

# ==================== 同步修改：支持售卖合成出的素材 ====================
# 找到原来的 @app_api.post("/api/game/shop/sell") 接口，在里面的判断分支增加 material 支持：
@app_api.post("/api/game/shop/sell")
def sell_item(req: TradeRequest):
    profile = MOCK_PLAYER_DB.get(req.player_id)
    if not profile: raise HTTPException(status_code=404, detail="未找到存档")

    inventory = profile["inventory"]
    sell_income = 0

    if req.item_type == "weapon":
        if req.item_id not in inventory["weapons"]: raise HTTPException(400, "未拥有该武器")
        if len(inventory["weapons"]) <= 1: raise HTTPException(400, "必须保留至少一件武器用于防身")
        sell_income = WEAPONS[req.item_id]["value"] // 2
        profile["inventory"]["weapons"].remove(req.item_id)
    elif req.item_type == "armor":
        if req.item_id not in inventory["armors"]: raise HTTPException(400, "未拥有该护甲")
        if req.item_id == "0": raise HTTPException(400, "平民布衣无法变现")
        if len(inventory["armors"]) <= 1: raise HTTPException(400, "必须保留至少一件防具")
        sell_income = ARMORS[req.item_id]["value"] // 2
        profile["inventory"]["armors"].remove(req.item_id)
    elif req.item_type == "item":
        curr_count = inventory["items"].get(req.item_id, 0)
        if curr_count <= 0: raise HTTPException(400, "背包里没有这个消耗品了")
        sell_income = ITEMS[req.item_id]["value"] // 2
        profile["inventory"]["items"][req.item_id] = curr_count - 1
    elif req.item_type == "material":  # 👈 新增：回收变卖自制素材
        mat_dict = inventory.get("materials", {})
        if req.item_id not in mat_dict or mat_dict[req.item_id]["count"] <= 0:
            raise HTTPException(400, "素材不存在")
        sell_income = mat_dict[req.item_id]["value"] // 2
        mat_dict[req.item_id]["count"] -= 1
        if mat_dict[req.item_id]["count"] <= 0:
            del mat_dict[req.item_id]
    else:
        raise HTTPException(400, "未知商品大类")

    profile["gold"] += sell_income

    if req.thread_id:
        room = next((r for r in ACTIVE_ROOMS.values() if r.thread_id == req.thread_id), None)
        if room: return get_ws_snapshot(room)

    return {"status": "success", "profile": profile}


# ==========================================
# WebSocket 联机双端核心控制器
# ==========================================

@app_api.websocket("/ws/room/{room_id}/{player_id}")
async def ws_room(websocket: WebSocket, room_id: str, player_id: str):
    await websocket.accept()
    room = ACTIVE_ROOMS.get(room_id)
    if not room:
        await websocket.send_json({"event": "error", "msg": "房间已销毁"})
        await websocket.close()
        return

    is_p1 = (player_id == room.p1_id)
    if is_p1:
        room.p1_ws = websocket
    else:
        room.p2_ws = websocket

    # 1. 注入 Langfuse 追踪配置，绑定从 fight.py 导入的 langfuse_handler 实例
    from fight import langfuse_handler, langfuse
    config = {
        "configurable": {"thread_id": room.thread_id},
        "callbacks": [langfuse_handler],
        "run_name": f"WS_Room_{room_id}_{room.mode}"
    }

    # PvP 模式下，当且仅当 1P 和 2P 两个 WebSocket 均成功连接时，再开启 LangGraph 实例
    if not room.is_started and room.mode == "PvP" and room.p1_ws and room.p2_ws:
        room.is_started = True
        fight_app.invoke({
            "messages": [],
            "player_id": room.p1_id,
            "p2_id": room.p2_id,
            "game_mode": room.mode
        }, config=config)
        await room.broadcast({"event": "game_start", "snapshot": get_ws_snapshot(room)})

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            # --- 阶段 1：出战配装双端同步锁 ---
            if action == "prep":
                prep_payload = {
                    "weapon_id": data.get("weapon_id"),
                    "armor_id": data.get("armor_id"),
                    "item_id": data.get("item_id")
                }
                if is_p1:
                    room.p1_prep = prep_payload
                else:
                    room.p2_prep = prep_payload

                await room.broadcast({"event": "player_ready", "player_id": player_id, "phase": "prep"})

                if room.mode == "PvE":
                    room.p2_prep = "AI_SKIP"

                # 双方都已经提交完配装，进行数据组装并驱动 LangGraph
                if room.p1_prep and room.p2_prep:
                    p1_prof = MOCK_PLAYER_DB[room.p1_id]
                    p1_cls = CHARACTERS[p1_prof["character_id"]]
                    p1_arm = ARMORS[room.p1_prep["armor_id"]]

                    updates = {
                        "player_class": p1_cls,
                        "player_weapon": WEAPONS[room.p1_prep["weapon_id"]],
                        "player_armor": p1_arm,
                        "player_hp": p1_cls["hp"] + p1_arm["hp_bonus"],
                        "player_mp": p1_cls["mp"],
                        "player_status": "正常",
                        "player_item": ITEMS[room.p1_prep["item_id"]] if room.p1_prep.get("item_id") else None,
                        "player_item_count": p1_prof["inventory"]["items"].get(room.p1_prep.get("item_id"),
                                                                               0) if room.p1_prep.get("item_id") else 0
                    }

                    if room.mode == "PvP":
                        p2_prof = MOCK_PLAYER_DB[room.p2_id]
                        p2_cls = CHARACTERS[p2_prof["character_id"]]
                        p2_arm = ARMORS[room.p2_prep["armor_id"]]
                        updates.update({
                            "ai_class": p2_cls,
                            "ai_weapon": WEAPONS[room.p2_prep["weapon_id"]],
                            "ai_armor": p2_arm,
                            "ai_hp": p2_cls["hp"] + p2_arm["hp_bonus"],
                            "ai_mp": p2_cls["mp"],
                            "ai_status": "正常",
                            "ai_item": ITEMS[room.p2_prep["item_id"]] if room.p2_prep.get("item_id") else None,
                            "ai_item_count": p2_prof["inventory"]["items"].get(room.p2_prep.get("item_id"),
                                                                               0) if room.p2_prep.get("item_id") else 0
                        })

                    fight_app.update_state(config, updates)

                    # 驱动 LangGraph 运行到下一处中断（PlayerAction）
                    for _ in fight_app.stream(None, config=config): pass

                    room.p1_prep = None
                    room.p2_prep = None
                    await room.broadcast({"event": "snapshot", "snapshot": get_ws_snapshot(room)})

            # --- 阶段 2：动作出招双端同步锁 ---
            elif action == "combat":
                if is_p1:
                    room.p1_act = data.get("action_key")
                else:
                    room.p2_act = data.get("action_key")

                await room.broadcast({"event": "player_ready", "player_id": player_id, "phase": "combat"})

                if room.mode == "PvE":
                    room.p2_act = "AI_SKIP"

                # 双方行动均已录入缓冲区，合并指令执行图判定
                if room.p1_act and room.p2_act:
                    state_values = fight_app.get_state(config).values

                    def build_act(key, weapon):
                        if key == "0":
                            return {"id": "0", "name": "普通攻击", "cost": 0, "type": "attack", "multiplier": 1.0}
                        if key == "9":
                            return {"id": "9", "name": "战术防卫", "cost": 0, "type": "defense"}
                        if key == "i":
                            return {"id": "item", "name": "使用物品", "cost": 0, "type": "item"}
                        skill = next(s for s in weapon['skills'] if s['id'] == key)
                        return {
                            "id": skill['id'],
                            "name": f"技能:{skill['name']}",
                            "cost": skill['cost'],
                            "type": "skill",
                            "multiplier": skill['multiplier']
                        }

                    updates = {"player_action": build_act(room.p1_act, state_values['player_weapon'])}
                    if room.mode == "PvP":
                        updates["ai_action"] = build_act(room.p2_act, state_values['ai_weapon'])

                    fight_app.update_state(config, updates)

                    # 执行裁判逻辑与数值解算
                    for _ in fight_app.stream(None, config=config): pass

                    room.p1_act = None
                    room.p2_act = None

                    # 获取最新对决快照并同步广播
                    snapshot = get_ws_snapshot(room)
                    if snapshot.get("game_over"):
                        _record_npc_combat_outcome(room)
                        # The relationship in npc_enemy is part of the snapshot, so
                        # refresh it after persistence for the finishing client UI.
                        snapshot = get_ws_snapshot(room)
                    await room.broadcast({"event": "snapshot", "snapshot": snapshot})

                    # 若对决在当前回合决出胜负，刷新并上传 Langfuse 日志
                    if snapshot.get("game_over"):
                        try:
                            langfuse.flush()
                            print(f"[房间 {room.room_id}] 决斗已分出胜负，对战日志已上传至 Langfuse。")
                        except Exception as e:
                            print(f"[系统提示] Langfuse 日志刷新失败: {e}")

    except WebSocketDisconnect:
        # P1 或 P2 有任何一人连接中断，销毁房间并广播
        await room.broadcast({"event": "error", "msg": "对手已断开连接，决斗房间销毁！"})
        ACTIVE_ROOMS.pop(room_id, None)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app_api", host="0.0.0.0", port=8000, reload=True)
