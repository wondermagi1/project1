"""配置与 .env 加载的单元测试。"""

from __future__ import annotations

import contextlib
import os
import unittest
from pathlib import Path
from unittest import mock

from agent.config import AgentConfig, load_dotenv, load_env_file
from tests import make_workspace, remove_workspace


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_workspace("config-")

    def tearDown(self) -> None:
        remove_workspace(self.root)

    def _write_env(self, directory: Path, text: str) -> Path:
        path = directory / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    @contextlib.contextmanager
    def _isolated(self):
        """隔离环境：只让 from_env 在测试目录里查找 .env。

        开发机上项目根目录通常有一份填了真实密钥的 .env，
        如果不隔离，这些用例的结果就会随本机配置变化。
        """

        with mock.patch(
            "agent.config.load_dotenv", side_effect=lambda dirs: load_dotenv([self.root])
        ):
            yield

    # ------------------------------------------------------------- .env 解析
    def test_load_env_file_parses_common_syntax(self) -> None:
        path = self._write_env(
            self.root,
            '# 注释行\n'
            'CODE_AGENT_API_KEY=sk-test-1234567890\n'
            'CODE_AGENT_MODEL="deepseek-chat"\n'
            '\n'
            "export CODE_AGENT_BASE_URL='https://api.deepseek.com/v1'\n"
            "这行没有等号\n",
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = load_env_file(path)
            self.assertEqual(loaded["CODE_AGENT_API_KEY"], "sk-test-1234567890")
            self.assertEqual(loaded["CODE_AGENT_MODEL"], "deepseek-chat")
            self.assertEqual(loaded["CODE_AGENT_BASE_URL"], "https://api.deepseek.com/v1")
            self.assertEqual(os.environ["CODE_AGENT_MODEL"], "deepseek-chat")

    def test_existing_env_var_wins_over_env_file(self) -> None:
        path = self._write_env(self.root, "CODE_AGENT_API_KEY=from-file\n")
        with mock.patch.dict(os.environ, {"CODE_AGENT_API_KEY": "from-system"}, clear=True):
            loaded = load_env_file(path)
            self.assertEqual(loaded, {})
            self.assertEqual(os.environ["CODE_AGENT_API_KEY"], "from-system")

    def test_override_flag_forces_file_value(self) -> None:
        path = self._write_env(self.root, "CODE_AGENT_API_KEY=from-file\n")
        with mock.patch.dict(os.environ, {"CODE_AGENT_API_KEY": "from-system"}, clear=True):
            loaded = load_env_file(path, override=True)
            self.assertEqual(loaded["CODE_AGENT_API_KEY"], "from-file")
            self.assertEqual(os.environ["CODE_AGENT_API_KEY"], "from-file")

    def test_missing_file_is_not_an_error(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_env_file(self.root / "nope.env"), {})

    def test_load_dotenv_searches_multiple_directories(self) -> None:
        first = self.root / "a"
        second = self.root / "b"
        first.mkdir()
        second.mkdir()
        self._write_env(first, "CODE_AGENT_MODEL=first\n")
        self._write_env(second, "CODE_AGENT_API_KEY=second\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = load_dotenv([first, second])
            self.assertEqual(loaded["CODE_AGENT_MODEL"], "first")
            self.assertEqual(loaded["CODE_AGENT_API_KEY"], "second")

    # ------------------------------------------------------- 配置构造与优先级
    def test_from_env_reads_dotenv_from_workspace(self) -> None:
        self._write_env(
            self.root,
            "CODE_AGENT_API_KEY=sk-abcdefghijklmn\n"
            "CODE_AGENT_MODEL=deepseek-chat\n"
            "CODE_AGENT_BASE_URL=https://api.deepseek.com/v1\n",
        )
        with self._isolated(), mock.patch.dict(os.environ, {}, clear=True):
            config = AgentConfig.from_env(workspace=self.root)
            self.assertEqual(config.active_provider, "openai")
            self.assertTrue(config.is_online)
            self.assertEqual(config.model, "deepseek-chat")
            self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
            self.assertEqual(config.masked_key(), "sk-a...klmn")

    def test_cli_override_beats_dotenv(self) -> None:
        self._write_env(self.root, "CODE_AGENT_API_KEY=sk-from-file\nCODE_AGENT_MODEL=from-file\n")
        with self._isolated(), mock.patch.dict(os.environ, {}, clear=True):
            config = AgentConfig.from_env(workspace=self.root, model="from-cli")
            self.assertEqual(config.model, "from-cli")
            self.assertEqual(config.api_key, "sk-from-file")

    def test_without_key_falls_back_to_mock(self) -> None:
        with self._isolated(), mock.patch.dict(os.environ, {}, clear=True):
            config = AgentConfig.from_env(workspace=self.root)
            self.assertEqual(config.active_provider, "mock")
            self.assertFalse(config.is_online)
            self.assertEqual(config.masked_key(), "(未配置)")

    def test_invalid_values_are_normalized(self) -> None:
        config = AgentConfig(provider="unknown", max_iterations=999, max_retries=0, exec_timeout=0.1)
        self.assertEqual(config.provider, "auto")
        self.assertEqual(config.max_iterations, 20)
        self.assertEqual(config.max_retries, 1)
        self.assertGreaterEqual(config.exec_timeout, 0.5)

    def test_masked_key_does_not_leak_full_secret(self) -> None:
        config = AgentConfig(api_key="sk-1234567890abcdef")
        masked = config.masked_key()
        self.assertNotIn("1234567890", masked)
        self.assertIn("...", masked)

    def test_shipped_env_example_has_no_secret(self) -> None:
        """仓库自带的 .env.example 不能包含真实密钥（只允许空值或公开默认值）。"""

        template = Path(__file__).resolve().parents[1] / ".env.example"
        self.assertTrue(template.is_file(), "缺少 .env.example 模板文件")
        raw = template.read_text(encoding="utf-8")
        self.assertNotIn("sk-", raw, ".env.example 里出现了形似密钥的内容")
        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = load_env_file(template)
        self.assertEqual(loaded.get("CODE_AGENT_API_KEY", ""), "", ".env.example 的 API Key 必须留空")

    def test_local_env_file_is_ignored_and_not_packaged(self) -> None:
        """本地 .env 存放真实密钥，必须被 git 忽略、并被打包脚本排除。"""

        repo_root = Path(__file__).resolve().parents[1]
        ignore_rules = (repo_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignore_rules)
        package_script = (repo_root / "scripts" / "package.py").read_text(encoding="utf-8")
        self.assertIn('".env"', package_script)


if __name__ == "__main__":
    unittest.main()
