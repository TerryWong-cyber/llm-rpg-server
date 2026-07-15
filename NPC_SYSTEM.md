# NPC、记忆与世界事件系统

## 边界

规则层决定关系变化、触发条件、战斗面板和奖励；LLM 只负责角色化表达、回忆引用和从已解锁剧情钩子中选择可提及的一条。这样既有自然对话，也不会让模型凭空改变数值或发放物品。

```text
地图/战斗/对话事件
        |
        v
NPCInteractionService  --确定性规则--> 关系、剧情钩子、战斗资格
        |                                      |
        |                                      v
        +-- NPCDialogueService <--- NPC 与相关记忆   既有 PvE 战斗图
        |              |
        |              v
        +-------- LLM 的结构化台词
        |
        v
InMemoryWorldRepository（可替换为数据库）
  - NPC 资料
  - NPC 对玩家的记忆
  - 玩家经历记忆
  - 公开世界事实
```

## 模块

- `npc_models.py`：纯数据契约。新增种族、职业、触发条件或任务字段从这里开始。
- `npc_seed.py`：三个示范 NPC 内容；以后可改成 JSON、CMS 或数据库加载器。
- `world_repository.py`：存储接口的内存实现；迁移到 SQLite/MySQL 时替换该文件，不改对话和规则代码。
- `npc_dialogue.py`：LLM 适配器；模型不可用时有稳定的回退台词。
- `npc_interaction.py`：关系变化、双向记忆、剧情钩子和战斗触发的规则层。
- `server.py`：薄 API 适配层，并把经过授权的 NPC 战斗注入现有 PvE 战斗图。

## API 流程

1. `GET /api/world/npcs?terrain_id=5` 获取沙漠附近 NPC。
2. `POST /api/world/npcs/darok_blacksalt/dialogue`，请求体为 `{"player_id":"...","message":"交出货单，否则我就动手。"}`。
3. 响应中的 `combat_trigger` 会包含 `darok_defend_cargo`；只有该触发器被规则层武装后，才能调用 `POST /api/world/npcs/darok_blacksalt/combat/start`。
4. 返回的 `room_id` 和 `websocket_path` 可直接接入现有战斗 UI。结算后，战果会写入 NPC、玩家和公开世界记忆。

调试接口：

- `GET /api/world/npcs/{npc_id}/memories?player_id=...`
- `GET /api/world/players/{player_id}/memories`
- `GET /api/world/facts`

## 扩展建议

给新 NPC 仅需添加资料、剧情钩子和数据化触发条件。若要实现阵营、日程、谣言或任务奖励，优先新增独立的规则服务订阅 `world_facts`，不要让 LLM 直接写背包、金币或胜负结果。
