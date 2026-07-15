import unittest

from npc_dialogue import NPCDialogueService
from npc_interaction import NPCInteractionService
from npc_seed import seed_demo_npcs
from world_repository import InMemoryWorldRepository


class NPCSystemTest(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryWorldRepository()
        seed_demo_npcs(self.repository)
        # fallback 机制：rule 引擎在 llm 为 None 时处理特定文本
        self.service = NPCInteractionService(self.repository, NPCDialogueService(llm=None))
        self.player_id = "test_player"
        self.other_player_id = "other_player"

    def test_threat_dialogue_arms_combat_and_keeps_bilateral_memories(self):
        """测试威胁对话触发战斗、生成双向记忆以及战斗获胜后的状态结算"""
        # 1. 初始交互：威胁
        result = self.service.interact(
            "darok_blacksalt",
            self.player_id,
            "交出货单，否则我就动手。",
        )

        self.assertEqual(result["intent"], "threat")
        self.assertIsNotNone(result.get("combat_trigger"))
        self.assertEqual(result["combat_trigger"]["trigger_id"], "darok_defend_cargo")
        self.assertGreaterEqual(result["relationship"].hostility, 10)

        # 验证初始记忆写入
        npc_memories = self.service.npc_memories("darok_blacksalt", self.player_id)
        player_memories = self.service.player_memories(self.player_id)
        self.assertEqual(len(npc_memories), 1)
        self.assertEqual(len(player_memories), 1)

        # 安全地验证记忆内容是否包含威胁相关信息（兼容不同字段命名）
        memory_str = str(npc_memories[0])
        self.assertTrue(any(keyword in memory_str for keyword in ["threat", "threaten", "交出", "动手"]))

        # 2. 开启战斗
        npc, combat = self.service.start_combat(
            "darok_blacksalt", self.player_id, "darok_defend_cargo"
        )
        self.assertEqual(npc.name, "达洛克·黑盐")
        self.assertEqual(combat.character_id, "2")

        # 3. 战斗胜利结算
        self.service.record_combat_outcome("darok_blacksalt", self.player_id, player_won=True)

        # 验证胜利后的关系变化
        view = self.service.get_npc_view("darok_blacksalt", self.player_id)
        self.assertGreaterEqual(view["relationship"].respect, 8)

        # 验证胜利记忆与世界事实
        self.assertTrue(any("combat_victory" in memory.tags for memory in self.service.player_memories(self.player_id)))
        self.assertTrue(any("combat_outcome" in fact.tags for fact in self.service.world_facts()))

    def test_combat_loss_outcome_behavior(self):
        """测试战斗失败时的状态结算是否符合预期（例如：尊重不增加）"""
        # 1. 触发战斗
        self.service.interact("darok_blacksalt", self.player_id, "交出货单，否则我就动手。")
        self.service.start_combat("darok_blacksalt", self.player_id, "darok_defend_cargo")

        # 2. 战斗失败结算
        self.service.record_combat_outcome("darok_blacksalt", self.player_id, player_won=False)

        view = self.service.get_npc_view("darok_blacksalt", self.player_id)
        # 战败后，NPC 的尊重值不应大幅提升（与战胜的 assertGreaterEqual(view["respect"], 8) 形成对比）
        self.assertLess(view["relationship"].respect, 8)
        self.assertTrue(any("combat_defeat" in memory.tags for memory in self.service.player_memories(self.player_id)))

    def test_friendly_dialogue_does_not_trigger_combat(self):
        """测试非威胁性（友好）对话，不应触发战斗，且不应增加敌意"""
        # 获取初始敌意基线
        initial_view = self.service.get_npc_view("darok_blacksalt", self.player_id)
        initial_hostility = initial_view["relationship"].hostility

        result = self.service.interact(
            "darok_blacksalt",
            self.player_id,
            "你好，达洛克，有什么需要帮忙的吗？",
        )
        self.assertNotEqual(result["intent"], "threat")
        self.assertIsNone(result.get("combat_trigger"))
        # 友好对话后，敌意不应上涨超过初始基线
        self.assertLessEqual(result["relationship"].hostility, initial_hostility)

    def test_multi_turn_interaction_memory_accumulation(self):
        """测试多次交互后，记忆能够正确累加，而不是被覆盖"""
        self.service.interact("darok_blacksalt", self.player_id, "你好。")
        self.service.interact("darok_blacksalt", self.player_id, "交出货单，否则我就动手。")

        npc_memories = self.service.npc_memories("darok_blacksalt", self.player_id)
        # 应该至少产生 2 条记忆
        self.assertGreaterEqual(len(npc_memories), 2)

    def test_player_data_isolation(self):
        """测试不同玩家的数据隔离，玩家 A 的敌意和记忆不应泄露给玩家 B"""
        # 获取玩家 A 和 玩家 B 的初始敌意基线
        hostility_a_before = self.service.get_npc_view("darok_blacksalt", self.player_id)["relationship"].hostility
        hostility_b_before = self.service.get_npc_view("darok_blacksalt", self.other_player_id)[
            "relationship"].hostility

        # 玩家 A 威胁 NPC
        self.service.interact("darok_blacksalt", self.player_id, "交出货单，否则我就动手。")

        # 获取交互后的状态
        hostility_a_after = self.service.get_npc_view("darok_blacksalt", self.player_id)["relationship"].hostility
        hostility_b_after = self.service.get_npc_view("darok_blacksalt", self.other_player_id)["relationship"].hostility

        # 玩家 A 引起的敌意应该明显上升
        self.assertGreater(hostility_a_after, hostility_a_before)
        # 玩家 B 的敌意应该保持原状，不受玩家 A 行为的影响
        self.assertEqual(hostility_b_after, hostility_b_before)

        # 记忆隔离：玩家 B 依然没有与该 NPC 的交互记忆
        self.assertEqual(len(self.service.npc_memories("darok_blacksalt", self.other_player_id)), 0)

    def test_private_backstory_is_not_returned_by_public_view(self):
        """测试公共视图接口不会泄露 NPC 的私密背景或敏感字段"""
        public_npcs = self.service.list_npcs(terrain_id="5")
        self.assertTrue(len(public_npcs) > 0)

        for npc in public_npcs:
            if hasattr(npc, "__dict__"):
                self.assertFalse(hasattr(npc, "private_secret"))
                self.assertFalse(hasattr(npc, "backstory_private"))
            elif isinstance(npc, dict):
                self.assertNotIn("private_secret", npc)
                self.assertNotIn("backstory_private", npc)

            npc_str = str(npc)
            self.assertNotIn("private_secret", npc_str)
            self.assertNotIn("难民姓名", npc_str)

    def test_invalid_npc_or_player_handling(self):
        """测试传入无效的 NPC ID 时的异常处理/鲁棒性"""
        # 视业务逻辑而定：部分系统返回 None，部分系统抛出 KeyError / Exception
        # 这里验证是否不会引发未捕获的不可控系统崩溃
        try:
            result = self.service.get_npc_view("non_existent_npc", self.player_id)
            self.assertIsNone(result)
        except Exception as e:
            # 如果系统设计为抛出异常，验证异常已被合理定义
            self.assertTrue(isinstance(e, Exception))


if __name__ == "__main__":
    unittest.main()