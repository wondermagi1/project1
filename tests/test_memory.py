"""对话记忆的单元测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from agent.memory import ConversationMemory, safe_session_id
from tests import make_workspace, remove_workspace


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_workspace("memory-")

    def tearDown(self) -> None:
        remove_workspace(self.root)

    def test_safe_session_id(self) -> None:
        self.assertEqual(safe_session_id("my session/1"), "my_session_1")
        self.assertEqual(safe_session_id("   "), "default")

    def test_turn_counting_and_last_user_text(self) -> None:
        memory = ConversationMemory(system_prompt="sys")
        memory.add({"role": "user", "content": "你好"})
        memory.add({"role": "assistant", "content": "你好，有什么可以帮你？"})
        memory.add({"role": "user", "content": "审查 a.py"})
        self.assertEqual(memory.turns(), 2)
        self.assertEqual(memory.last_user_text(), "审查 a.py")

    def test_clear_keeps_system_prompt(self) -> None:
        memory = ConversationMemory(system_prompt="sys")
        memory.add({"role": "user", "content": "hi"})
        memory.clear()
        self.assertEqual(len(memory), 1)
        self.assertEqual(memory.messages()[0]["role"], "system")

    def test_trim_drops_orphan_tool_messages(self) -> None:
        memory = ConversationMemory(system_prompt="sys", max_messages=8)
        for index in range(6):
            memory.add({"role": "user", "content": f"u{index}"})
            memory.add(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{index}",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                }
            )
            memory.add(
                {
                    "role": "tool",
                    "tool_call_id": f"c{index}",
                    "name": "read_file",
                    "content": "{}",
                }
            )
            memory.add({"role": "assistant", "content": f"a{index}"})
        messages = memory.messages()
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertLessEqual(len(messages), 8)

    def test_save_and_load_roundtrip(self) -> None:
        memory = ConversationMemory(
            session_id="demo", session_dir=self.root, system_prompt="sys"
        )
        memory.add({"role": "user", "content": "审查 a.py"})
        memory.add({"role": "assistant", "content": "好的"})
        target = memory.save()
        self.assertIsNotNone(target)
        self.assertTrue(Path(target).is_file())

        loaded = ConversationMemory.load("demo", self.root, system_prompt="sys")
        self.assertEqual(len(loaded), 3)
        self.assertEqual(loaded.last_user_text(), "审查 a.py")

    def test_load_missing_or_broken_file_falls_back(self) -> None:
        loaded = ConversationMemory.load("missing", self.root, system_prompt="sys")
        self.assertEqual(len(loaded), 1)

        (self.root / "broken.json").write_text("{ not json", encoding="utf-8")
        broken = ConversationMemory.load("broken", self.root, system_prompt="sys")
        self.assertEqual(len(broken), 1)

    def test_tool_result_lookup(self) -> None:
        memory = ConversationMemory()
        memory.add({"role": "user", "content": "审查 a.py"})
        memory.add(
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "analyze_code",
                "content": '{"ok": true, "score": 80}',
            }
        )
        self.assertEqual(memory.tool_result("analyze_code")["score"], 80)
        self.assertIsNone(memory.tool_result("read_file"))

    def test_list_sessions(self) -> None:
        memory = ConversationMemory(session_id="one", session_dir=self.root)
        memory.add({"role": "user", "content": "hi"})
        memory.save()
        sessions = ConversationMemory.list_sessions(self.root)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "one")

    def test_current_long_turn_survives_trimming(self) -> None:
        memory = ConversationMemory(system_prompt="sys", max_messages=8)
        memory.add({"role": "user", "content": "old"})
        memory.add({"role": "assistant", "content": "old result"})
        memory.add({"role": "user", "content": "current task"})
        for i in range(12):
            memory.add({"role": "assistant", "tool_calls": [{"id": str(i)}]})
            memory.add({"role": "tool", "tool_call_id": str(i), "content": "{}"})
        messages = memory.messages()
        self.assertEqual(messages[1]["content"], "current task")
        self.assertEqual(len([m for m in messages if m["role"] == "tool"]), 12)
        memory.add({"role": "user", "content": "next"})
        self.assertEqual(len(memory), 2)

    def test_non_object_session_is_ignored(self) -> None:
        (self.root / "bad.json").write_text("[]", encoding="utf-8")
        self.assertEqual(len(ConversationMemory.load("bad", self.root, "sys")), 1)
        self.assertEqual(ConversationMemory.list_sessions(self.root), [])

    def test_message_copy_does_not_mutate_tool_calls(self) -> None:
        memory = ConversationMemory()
        memory.add({"role": "user", "content": "hi"})
        memory.add({"role": "assistant", "tool_calls": [{"id": "original"}]})
        memory.messages()[1]["tool_calls"][0]["id"] = "changed"
        self.assertEqual(memory.messages()[1]["tool_calls"][0]["id"], "original")


if __name__ == "__main__":
    unittest.main()
