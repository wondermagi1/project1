"""验证交付包内容，不包含本地密钥或会话。"""

import unittest
import zipfile

from scripts.package import build_package, iter_files, validate_package_name
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
        target = self.root / "submission.zip"
        self.assertEqual(build_package(self.root, target, "学号姓名"), 5)
        with zipfile.ZipFile(target) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("学号姓名/README.md", archive.namelist())

    def test_missing_docs_fail_before_creating_package(self):
        target = self.root / "submission.zip"
        with self.assertRaisesRegex(ValueError, "README"):
            build_package(self.root, target, "demo")
        self.assertFalse(target.exists())

    def test_package_name_cannot_escape_output_directory(self):
        for name in ("../escape", "a/b", "a\\b", "", "CON", "demo."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_package_name(name)
