"""任务模式的单元测试：确保作业给出的五个方向都有对应实现。"""

from __future__ import annotations

import unittest

from agent.modes import (
    AUTO_MODE,
    DEFAULT_MODE,
    MODES,
    build_system_prompt,
    detect_mode,
    extract_mode_marker,
    list_modes,
    resolve_mode,
    strip_mode_marker,
)

DIRECTIONS = (
    "代码审查 Agent",
    "代码解释 Agent",
    "代码生成 Agent",
    "测试生成 Agent",
    "重构建议 Agent",
)


class ModeTests(unittest.TestCase):
    def test_five_directions_are_all_covered(self) -> None:
        covered = {mode.direction for mode in MODES.values()}
        for direction in DIRECTIONS:
            self.assertIn(direction, covered, f"缺少方向：{direction}")

    def test_detect_mode_by_keywords(self) -> None:
        cases = {
            "审查 examples/a.py": "review",
            "解释一下这段代码的逻辑": "explain",
            "写一个 quicksort 函数": "generate",
            "为 examples/a.py 生成单元测试": "test",
            "这段代码怎么重构": "refactor",
        }
        for text, expected in cases.items():
            self.assertEqual(detect_mode(text), expected, f"输入：{text}")

    def test_test_intent_wins_over_generate(self) -> None:
        # 「生成单元测试」同时包含「生成」和「测试」，必须判为测试模式
        self.assertEqual(detect_mode("生成单元测试"), "test")

    def test_unknown_text_falls_back_to_default_mode(self) -> None:
        self.assertEqual(detect_mode("帮我看看这个文件"), DEFAULT_MODE)

    def test_resolve_mode_normalizes_input(self) -> None:
        self.assertEqual(resolve_mode("refactor"), "refactor")
        self.assertEqual(resolve_mode("  TEST "), "test")
        self.assertEqual(resolve_mode(None), AUTO_MODE)
        self.assertEqual(resolve_mode("nonsense"), AUTO_MODE)

    def test_system_prompt_carries_mode_specific_rules(self) -> None:
        test_prompt = build_system_prompt("test")
        self.assertIn("测试生成", test_prompt)
        self.assertIn("run_tests", test_prompt)

        refactor_prompt = build_system_prompt("refactor")
        self.assertIn("重构建议", refactor_prompt)
        self.assertIn("diff_files", refactor_prompt)

        auto_prompt = build_system_prompt(AUTO_MODE)
        self.assertIn("自动模式", auto_prompt)
        for name in ("review", "explain", "generate", "test", "refactor"):
            self.assertIn(name, auto_prompt)

    def test_mode_marker_roundtrip(self) -> None:
        text = "[mode:refactor]\n重构 a.py"
        self.assertEqual(extract_mode_marker(text), "refactor")
        self.assertEqual(strip_mode_marker(text), "重构 a.py")
        self.assertIsNone(extract_mode_marker("没有标记"))

    def test_list_modes_puts_five_directions_first(self) -> None:
        names = [mode.name for mode in list_modes()]
        self.assertEqual(names[:5], ["review", "explain", "generate", "test", "refactor"])
        self.assertIn("ask", names)

    def test_every_mode_declares_tools_and_output_format(self) -> None:
        for name, mode in MODES.items():
            self.assertTrue(mode.suggested_tools, f"{name} 未声明建议工具")
            self.assertTrue(mode.output_format, f"{name} 未声明输出规范")
            self.assertTrue(mode.instructions, f"{name} 未声明执行要求")


if __name__ == "__main__":
    unittest.main()
