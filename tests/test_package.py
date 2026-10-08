"""验证交付包内容，不包含本地密钥或会话。"""

import unittest
import zipfile
import io
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.package import build_package, iter_files, validate_package_name, main
from tests import make_workspace, remove_workspace


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.root = make_workspace("package-")

    def tearDown(self):
        remove_workspace(self.root)

    def test_excludes_private_files_and_keeps_template(self):
        for name in ("README.md", "Design.md", "main.py", "webui.py", ".env", ".env.local", ".env.backup", ".env.example"):
            (self.root / name).write_text("test", encoding="utf-8")
        (self.root / ".agent_sessions").mkdir()
        (self.root / ".agent_sessions" / "private.json").write_text("{}")
        names = {p.name for p in iter_files(self.root)}
        self.assertIn(".env.example", names)
        self.assertTrue({".env", ".env.local", ".env.backup", "private.json"}.isdisjoint(names))
        target = self.root / "source.zip"
        self.assertEqual(build_package(self.root, target, "code-assistant-agent"), 5)
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("code-assistant-agent/README.md", archive.namelist())

    def test_missing_docs_fail_before_creating_package(self):
        target = self.root / "source.zip"
        with self.assertRaisesRegex(ValueError, "README"):
            build_package(self.root, target, "demo")
        self.assertFalse(target.exists())

    def test_package_name_cannot_escape_output_directory(self):
        for name in ("../escape", "a/b", "a\\b", "", "CON", "demo."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_package_name(name)

    def test_default_cli_creates_project_archive_in_dist(self):
        for name in ("README.md", "Design.md", "main.py", "webui.py"):
            (self.root / name).write_text("fixture", encoding="utf-8")
        with patch("scripts.package.__file__", str(self.root / "scripts" / "package.py")), redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)
        target = self.root / "dist" / "code-assistant-agent.zip"
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(len(archive.namelist()), 4)
