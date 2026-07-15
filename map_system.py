# map_system.py
import random
from typing import List, Dict
from config_loader import TERRAINS, RESOURCES


def generate_5x5_map() -> List[Dict]:
    """生成一个 5x5 的网格地图 (长度为 25 的数组)"""
    map_grid = []
    terrain_keys = list(TERRAINS.keys())

    for i in range(25):
        t_id = random.choice(terrain_keys)
        map_grid.append({
            "cell_id": i,
            "terrain_id": t_id,
            "is_gathered": False  # 是否已被采集过
        })
    return map_grid


def gather_from_cell(terrain_id: str) -> Dict[str, int]:
    """根据地形的掉落配置，掷骰子计算产出的资源及数量"""
    terrain_cfg = TERRAINS.get(terrain_id)
    if not terrain_cfg: return {}

    loot = {}
    for res_id, prob in terrain_cfg.get("drops", {}).items():
        if random.random() <= prob:
            # 随机产出 1 到 3 个对应资源
            loot[res_id] = random.randint(1, 3)

    return loot