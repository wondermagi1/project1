"""工具层的单元测试，重点覆盖边界情况与安全约束。"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from agent.config import AgentConfig
from agent.tools import build_default_registry
from tests import make_workspace, remove_workspace


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_workspace("tools-")
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "sample.py").write_text(
            textwrap.dedent(
                '''
                """样例模块。"""


                def add(left, right):
                    """返回两数之和。"""

                    return left + right


                # TODO: 补充异常分支
                def divide(left, right):
                    """返回两数之商。"""

                    return left / right
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        (self.root / "notes.txt").write_text("hello\nworld\n", encoding="utf-8")
        self.config = AgentConfig(workspace=self.root)
        self.registry = build_default_registry(self.config)

    def tearDown(self) -> None:
        remove_workspace(self.root)

    # ------------------------------------------------------------ read_file
    def test_read_file_returns_numbered_content(self) -> None:
        result = self.registry.execute("read_file", {"path": "notes.txt"})
        self.assertTrue(result["ok"])
        self.assertIn("    1 | hello", result["content"])
        self.assertEqual(result["total_lines"], 2)
        self.assertFalse(result["truncated"])

    def test_read_file_supports_line_range(self) -> None:
        result = self.registry.execute("read_file", {"path": "notes.txt", "start_line": 2})
        self.assertTrue(result["ok"])
        self.assertNotIn("hello", result["content"])
        self.assertIn("world", result["content"])

    def test_read_file_reports_missing_file(self) -> None:
        result = self.registry.execute("read_file", {"path": "nope.py"})
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])

    def test_read_file_rejects_directory(self) -> None:
        result = self.registry.execute("read_file", {"path": "pkg"})
        self.assertFalse(result["ok"])
        self.assertIn("目录", result["error"])

    def test_path_escape_is_rejected(self) -> None:
        result = self.registry.execute("read_file", {"path": "../outside.txt"})
        self.assertFalse(result["ok"])
        self.assertIn("越界", result["error"])

    def test_absolute_path_outside_workspace_is_rejected(self) -> None:
        outside = (self.root.parent / "outside.txt").resolve()
        result = self.registry.execute("read_file", {"path": str(outside)})
        self.assertFalse(result["ok"])
        self.assertIn("越界", result["error"])

    # ------------------------------------------------------------ list/search
    def test_list_dir_skips_cache_directories(self) -> None:
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
        result = self.registry.execute("list_dir", {"path": ".", "recursive": True})
        self.assertTrue(result["ok"])
        paths = [entry["path"] for entry in result["entries"]]
        self.assertIn("pkg/sample.py", paths)
        self.assertFalse(any("__pycache__" in path for path in paths))

    def test_search_in_files(self) -> None:
        result = self.registry.execute(
            "search_in_files", {"query": "TODO", "path": ".", "glob": "*.py"}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["matches"][0]["path"], "pkg/sample.py")

    def test_search_with_invalid_regex(self) -> None:
        result = self.registry.execute(
            "search_in_files", {"query": "([", "regex": True, "path": "."}
        )
        self.assertFalse(result["ok"])
        self.assertIn("正则", result["error"])

    # ------------------------------------------------------------ write_file
    def test_write_file_creates_new_file(self) -> None:
        result = self.registry.execute(
            "write_file", {"path": "out/new.txt", "content": "hello\n"}
        )
        self.assertTrue(result["ok"])
        self.assertTrue((self.root / "out" / "new.txt").is_file())

    def test_write_file_requires_overwrite_flag(self) -> None:
        first = self.registry.execute(
            "write_file", {"path": "notes.txt", "content": "new"}
        )
        self.assertFalse(first["ok"])
        second = self.registry.execute(
            "write_file", {"path": "notes.txt", "content": "new", "overwrite": True}
        )
        self.assertTrue(second["ok"])
        self.assertEqual((self.root / "notes.txt").read_text(encoding="utf-8"), "new")

    # ------------------------------------------------------------ run_python
    def test_run_python_success(self) -> None:
        result = self.registry.execute("run_python", {"code": "print(1 + 1)"})
        self.assertTrue(result["ok"])
        self.assertIn("2", result["stdout"])

    def test_run_python_reports_failure(self) -> None:
        result = self.registry.execute("run_python", {"code": "raise ValueError('boom')"})
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["returncode"], 0)
        self.assertIn("ValueError", result["stderr"])

    def test_run_python_runs_file(self) -> None:
        (self.root / "script.py").write_text("print('from file')\n", encoding="utf-8")
        result = self.registry.execute("run_python", {"path": "script.py"})
        self.assertTrue(result["ok"])
        self.assertIn("from file", result["stdout"])

    def test_run_python_timeout(self) -> None:
        result = self.registry.execute(
            "run_python", {"code": "while True:\n    pass\n", "timeout": 1}
        )
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])

    # ---------------------------------------------------------- analyze_code
    def test_analyze_code_from_path(self) -> None:
        result = self.registry.execute("analyze_code", {"path": "pkg/sample.py"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["language"], "python")
        self.assertEqual(result["metrics"]["functions"], 2)
        codes = {finding["code"] for finding in result["findings"]}
        self.assertIn("GEN002", codes)

    def test_analyze_code_from_snippet(self) -> None:
        result = self.registry.execute(
            "analyze_code", {"code": "def f(x=[]):\n    return x\n", "filename": "snippet.py"}
        )
        self.assertTrue(result["ok"])
        codes = {finding["code"] for finding in result["findings"]}
        self.assertIn("PY006", codes)

    # ------------------------------------------------------------- 其他边界
    def test_unknown_tool_returns_error(self) -> None:
        result = self.registry.execute("not_a_tool", {})
        self.assertFalse(result["ok"])
        self.assertIn("未知工具", result["error"])

    def test_read_only_mode_blocks_dangerous_tools(self) -> None:
        config = AgentConfig(workspace=self.root, read_only=True)
        registry = build_default_registry(config)
        self.assertFalse(registry.execute("write_file", {"path": "a.txt", "content": "x"})["ok"])
        self.assertFalse(registry.execute("run_python", {"code": "print(1)"})["ok"])
        self.assertTrue(registry.execute("read_file", {"path": "notes.txt"})["ok"])

    def test_no_exec_blocks_only_code_execution(self) -> None:
        config = AgentConfig(workspace=self.root, allow_exec=False)
        registry = build_default_registry(config)
        blocked = registry.execute("run_python", {"code": "print(1)"})
        self.assertFalse(blocked["ok"])
        self.assertIn("禁用", blocked["error"])
        # 写文件不属于「执行代码」，在非只读模式下仍然可用。
        self.assertTrue(registry.execute("write_file", {"path": "ok.txt", "content": "x"})["ok"])

    # ------------------------------------------------------------ run_tests
    def test_run_tests_reports_success(self) -> None:
        (self.root / "test_ok.py").write_text(
            "import unittest\n"
            "\n"
            "\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(1 + 1, 2)\n",
            encoding="utf-8",
        )
        result = self.registry.execute("run_tests", {"path": "test_ok.py"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["tests_run"], 1)
        self.assertEqual(result["passed_count"], 1)
        self.assertEqual(result["failure_count"], 0)

    def test_run_tests_reports_failures_without_failing_the_tool(self) -> None:
        (self.root / "test_bad.py").write_text(
            "import unittest\n"
            "\n"
            "\n"
            "class SampleTest(unittest.TestCase):\n"
            "    def test_boom(self):\n"
            "        self.assertEqual(1, 2)\n",
            encoding="utf-8",
        )
        result = self.registry.execute("run_tests", {"path": "test_bad.py"})
        self.assertTrue(result["ok"], "用例失败时工具本身仍应算执行成功")
        self.assertFalse(result["passed"])
        self.assertEqual(result["failure_count"], 1)
        self.assertIn("test_boom", result["failures"][0])
        self.assertEqual(result["passed_count"], 0)

    def test_run_tests_missing_path(self) -> None:
        result = self.registry.execute("run_tests", {"path": "no_such_file.py"})
        self.assertFalse(result["ok"])

    # ----------------------------------------------------------- diff_files
    def test_diff_files_reports_changes(self) -> None:
        result = self.registry.execute(
            "diff_files", {"left": "notes.txt", "content": "hello\nworld!\nnew line\n"}
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertGreaterEqual(result["added_lines"], 1)
        self.assertIn("-world", result["diff"])
        self.assertIn("+world!", result["diff"])

    def test_diff_files_identical_content(self) -> None:
        result = self.registry.execute(
            "diff_files", {"left": "notes.txt", "content": "hello\nworld\n"}
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertIn("没有差异", result["diff"])

    def test_diff_files_requires_a_second_side(self) -> None:
        result = self.registry.execute("diff_files", {"left": "notes.txt"})
        self.assertFalse(result["ok"])
        self.assertIn("right", result["error"])

    def test_tool_specs_are_valid_openai_schema(self) -> None:
        specs = self.registry.specs()
        self.assertEqual(len(specs), 10)
        for spec in specs:
            self.assertEqual(spec["type"], "function")
            self.assertIn("name", spec["function"])
            self.assertIn("parameters", spec["function"])


if __name__ == "__main__":
    unittest.main()
