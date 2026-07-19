# 技能系统

技能系统以 `src/llm_rpg_server/skills` 为领域边界，静态内容位于
`configs/skills/skills.json`。LLM 不参与技能学习、消耗、伤害、奖励或状态判定。

## 玩家状态

- `learned_skills` 保存技能、来源和学习时间。
- `equipped_skill_ids` 保存最多 5 个战斗主动技能。
- `exploration_effects` 保存带 UTC 开始时间和到期时间的探索状态。
- 普通攻击、防御和战斗物品不占技能槽；探索技能与被动技能也不占战斗槽。

## 获得方式

- `unlocks` 配置初始技能和种族等级技能。
- 技能书是 `catalog/game.json` 中带 `learn_skill_id` 的普通物品；学习成功才消耗，且禁止作为炼金材料。
- `trainers` 按 NPC ID 配置技能、金币和后续可扩展的关系要求。
- `StoryHook.skill_rewards` 支持任务技能奖励。
- 怪物掉落继续使用统一掉落表；精英和 Boss 可以显式配置低概率技能书。

## 效果与事件

技能效果是声明式 `effects` 列表。战斗执行器支持伤害包、回复和状态；世界事件支持
`skill_requirements`。`state_requirements` 可根据仍未到期的探索状态动态显示额外选项。

飞行等探索状态按现实秒数判定。服务端使用带时区的 UTC 时间计算 `expires_at`，客户端的
倒计时仅作展示，不能延长或伪造状态。

## 主要接口

- `POST /api/game/skills/learn-book`
- `POST /api/game/skills/learn-trainer`
- `GET /api/game/skills/trainer/{npc_id}`
- `POST /api/game/skills/equip`
- `POST /api/game/skills/cast-exploration`
- `POST /api/map/event-action`（可携带 `skill_id`）

新增技能时必须通过 `SkillRulesDocument` 校验，并确保技能书、任务、导师和事件引用都能在
应用启动阶段解析。涉及静态图标时，优先复用前端已经登记在
`public/assets/licenses/assets.manifest.json` 的 Game-icons.net 素材。
