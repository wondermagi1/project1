"""真实临时项目验证索引、跨文件扫描和 Git 证据。"""

import shutil
import subprocess
import unittest

from agent.agent import CodeAgent
from agent.config import AgentConfig
from agent.project import ProjectTools
from tests import make_workspace, remove_workspace


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.root = make_workspace("project-")
        self.config = AgentConfig(workspace=self.root, provider="mock")
        self.project = ProjectTools(self.config)
        (self.root / "a.py").write_text("def unsafe(items=[]):\n    return eval(str(items))\n", encoding="utf-8")
        (self.root / "b.py").write_text("def invalid(:\n", encoding="utf-8")
        (self.root / "README.md").write_text("# Demo", encoding="utf-8")

    def tearDown(self):
        remove_workspace(self.root)

    def test_index_skips_private_and_generated_files(self):
        for name in (".git", ".agent_sessions", ".test_workspaces", "node_modules", "generated"):
            directory = self.root / name
            directory.mkdir()
            (directory / "secret.py").write_text("secret")
        (self.root / ".env").write_text("SECRET=hidden")
        data = self.project.inventory()
        self.assertEqual({row["path"] for row in data["files"]}, {"a.py", "b.py", "README.md"})
        self.assertEqual(data["source_count"], 2)
        self.assertTrue(data["has_readme"])

    def test_scan_aggregates_real_findings_and_line_numbers(self):
        result = self.project.scan({})
        self.assertEqual(result["files_scanned"], 2)
        self.assertGreater(result["severity_summary"]["high"], 0)
        self.assertTrue(any(row["path"] == "b.py" and row["code"] == "SYN001" for row in result["findings"]))
        self.assertEqual(result["findings_total"], sum(result["severity_summary"][name] for name in ("high", "medium", "low")))
        self.assertTrue(all(row["line"] > 0 for row in result["findings"]))

    def test_scan_limit_and_binary_skip_are_explicit(self):
        limited = self.project.scan({"max_files": 1})
        self.assertTrue(limited["truncated"])
        self.assertEqual(limited["files_scanned"], 1)
        (self.root / "c.py").write_bytes(b"binary\x00text")
        result = self.project.scan({})
        self.assertEqual(result["skipped"][0]["path"], "c.py")

    def test_preview_rejects_secrets_and_path_escape(self):
        for name in (".env", "../README.md", ".git/config", str(self.root / "a.py")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.project.resolve_file(name)

    def test_mock_agent_scans_project_through_tool_loop(self):
        result = CodeAgent(self.config).run("扫描项目")
        self.assertEqual(result.stopped, "final")
        self.assertIn("项目质量扫描", result.answer)
        self.assertTrue(any(step.title == "调用工具 project_scan" for step in result.steps))

    @unittest.skipUnless(shutil.which("git"), "Git unavailable")
    def test_git_diff_and_changed_scan_match_repository_state(self):
        def git(*args):
            return subprocess.run([shutil.which("git"), *args], cwd=self.root, capture_output=True, check=True)
        git("init", "-q")
        git("add", "a.py", "b.py", "README.md")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        (self.root / "a.py").write_text("def changed():\n    return eval('1')\n", encoding="utf-8")
        git("add", "a.py")
        (self.root / "a.py").write_text("def changed():\n    return eval('2')\n", encoding="utf-8")
        (self.root / "new.py").write_text("print('new')", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=hidden")
        before = git("status", "--porcelain=v1").stdout
        result = self.project.git_diff({})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["changed_count"], 2)
        self.assertIn("未暂存修改", result["diff"])
        self.assertIn("已暂存修改", result["diff"])
        self.assertNotIn("SECRET", str(result))
        scan = self.project.scan({"changed_only": True})
        self.assertEqual({row["path"] for row in scan["files"]}, {"a.py", "new.py"})
        self.assertEqual(before, git("status", "--porcelain=v1").stdout)
