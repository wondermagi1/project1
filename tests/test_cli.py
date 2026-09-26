"""命令行入口的冒烟测试。"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_show_tools(self) -> None:
        code, output = self._run(["--show-tools", "--workspace", str(REPO_ROOT)])
        self.assertEqual(code, 0)
        for name in ("read_file", "analyze_code", "run_python"):
            self.assertIn(name, output)

    def test_one_shot_review_in_mock_mode(self) -> None:
        code, output = self._run(
            [
                "--provider",
                "mock",
                "--quiet",
                "--workspace",
                str(REPO_ROOT),
                "--session",
                "cli-test",
                "审查",
                "examples/buggy_sample.py",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("代码审查报告", output)
        self.assertIn("examples/buggy_sample.py", output)

    def test_json_output_contains_trace(self) -> None:
        code, output = self._run(
            [
                "--provider",
                "mock",
                "--quiet",
                "--json",
                "--workspace",
                str(REPO_ROOT),
                "--session",
                "cli-test-json",
                "审查",
                "examples/clean_sample.py",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["stopped"], "final")
        self.assertTrue(payload["steps"])
        self.assertIn("answer", payload)

    def test_list_sessions_does_not_fail_when_empty(self) -> None:
        code, output = self._run(["--list-sessions", "--workspace", str(REPO_ROOT)])
        self.assertEqual(code, 0)
        self.assertTrue(output.strip())


if __name__ == "__main__":
    unittest.main()
