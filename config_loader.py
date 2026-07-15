# config_loader.py
import os
import json

CONFIG_FILE_NAME = "game_configs.json"


def load_all_configs():
    # 增加基础兜底字典，包含全新的地形和资源配置
    fallback = {
        "CHARACTERS": {...},  # 保持原有
        "WEAPONS": {...},  # 保持原有
        "ARMORS": {...},  # 保持原有
        "ITEMS": {...},  # 保持原有
        "ENVIRONMENTS": [...],
        "GLOBAL_CONFIGS": {"initial_gold": 100},

        # 👇 新增：资源物品表
        "RESOURCES": {
            "mat_1": {"id": "mat_1", "name": "原木", "emoji": "🪵", "desc": "基础建筑与打造材料", "value": 5},
            "mat_2": {"id": "mat_2", "name": "石块", "emoji": "🪨", "desc": "坚硬的矿石", "value": 5},
            "mat_3": {"id": "mat_3", "name": "发光蘑菇", "emoji": "🍄", "desc": "炼金绝佳材料", "value": 15},
            "mat_4": {"id": "mat_4", "name": "鲜鱼", "emoji": "🐟", "desc": "恢复体力的食材", "value": 10},
            "mat_5": {"id": "mat_5", "name": "铁矿", "emoji": "⛏️", "desc": "锻造武器的刚需", "value": 20},
            "mat_6": {"id": "mat_6", "name": "草药", "emoji": "🌿", "desc": "制作治疗药水", "value": 8}
        },
        # 👇 新增：地形与掉落概率表 (drops: { 资源ID : 掉落概率(0-1) })
        "TERRAINS": {
            "1": {"id": "1", "name": "森林", "emoji": "🌲", "drops": {"mat_1": 0.8, "mat_3": 0.3}},
            "2": {"id": "2", "name": "山地", "emoji": "⛰️", "drops": {"mat_2": 0.7, "mat_5": 0.4}},
            "3": {"id": "3", "name": "海洋", "emoji": "🌊", "drops": {"mat_4": 0.9}},
            "4": {"id": "4", "name": "平原", "emoji": "🌾", "drops": {"mat_6": 0.7, "mat_1": 0.2}},
            "5": {"id": "5", "name": "沙漠", "emoji": "🏜️", "drops": {"mat_2": 0.5}}
        }
    }

    if os.path.exists(CONFIG_FILE_NAME):
        try:
            with open(CONFIG_FILE_NAME, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # 简单合并
                for k in fallback.keys():
                    if k in loaded: fallback[k] = loaded[k]
        except Exception as e:
            print(f"⚠️ 配置读取异常 ({e})")
    return fallback


_configs = load_all_configs()

CHARACTERS = _configs["CHARACTERS"]
WEAPONS = _configs["WEAPONS"]
ARMORS = _configs["ARMORS"]
ITEMS = _configs["ITEMS"]
ENVIRONMENTS = _configs["ENVIRONMENTS"]
GLOBAL_CONFIGS = _configs["GLOBAL_CONFIGS"]
RESOURCES = _configs["RESOURCES"]
TERRAINS = _configs["TERRAINS"]
