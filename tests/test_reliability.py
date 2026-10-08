"""导出、协议错误和配置边界的回归验证，不调用外部模型。"""

import argparse
import io
import json
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import patch

from agent.agent import AgentResult, AgentStep, CodeAgent
from agent.cli import _handle_command, main
from agent.config import AgentConfig
from agent.llm import LLMError, OpenAICompatibleClient
from agent.memory import ConversationMemory
from agent.reporting import export_report
from agent.tools import build_default_registry
from tests import make_workspace, remove_workspace


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.root = make_workspace("reliability-")
        self.config = AgentConfig(provider="mock", workspace=self.root)

    def tearDown(self):
        remove_workspace(self.root)

    def test_export_includes_result_trace_and_actual_provider(self):
        result = AgentResult("中文结果", [AgentStep("tool", "读取", "a.py")], 2, mode="review")
        for suffix in ("md", "json"):
            path = export_report(self.root / ("report." + suffix), result, "审查代码", "mock", "online-model")
            content = path.read_text(encoding="utf-8")
            self.assertIn("中文结果", content)
            self.assertIn("offline-rule-engine", content)
            self.assertNotIn("online-model", content)
            if suffix == "json":
                self.assertEqual(json.loads(content)["steps"][0]["detail"], "a.py")
            with self.assertRaises(FileExistsError):
                export_report(path, result, "new", "mock", "model")

    def test_cli_json_is_clean_without_quiet_and_can_export(self):
        output = io.StringIO()
        path = self.root / "result.json"
        with redirect_stdout(output):
            code = main(["--provider", "mock", "--workspace", str(self.root), "--json", "--export", str(path), "列出目录"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["stopped"], "final")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["task"], "列出目录")

    def test_cli_session_switch_preserves_mode(self):
        memory = ConversationMemory("first", self.config.session_dir, "sys")
        agent = CodeAgent(self.config, memory=memory, mode="review")
        with redirect_stdout(io.StringIO()):
            self.assertTrue(_handle_command("/session second", agent, argparse.Namespace()))
        self.assertEqual(agent.memory.session_id, "second")
        self.assertEqual(agent.mode, "review")
        self.assertIn("审查", agent.memory.system_prompt)

    def test_no_exec_blocks_test_runner(self):
        self.config.allow_exec = False
        registry = build_default_registry(self.config)
        with patch("agent.tools.subprocess.run") as runner:
            result = registry.execute("run_tests", {"path": "."})
        self.assertFalse(result["ok"])
        runner.assert_not_called()

    def test_malformed_model_responses_raise_readable_errors(self):
        responses = [[], None, {"choices": "bad"}, {"choices": [None]},
                     {"choices": [{"message": "bad"}]},
                     {"choices": [{"message": {"tool_calls": [None]}}]},
                     {"choices": [{"message": {"tool_calls": [{"function": "bad"}]}}]}]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(LLMError):
                OpenAICompatibleClient._parse_response(response)

    def test_retry_transient_failure_then_success(self):
        attempts = []
        def opener(request, timeout):
            attempts.append(request)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "busy", {}, io.BytesIO(b"busy"))
            return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
        client = OpenAICompatibleClient(self.config, opener)
        with patch.object(client, "_sleep") as sleep:
            self.assertEqual(client.chat([{"role": "user", "content": "hi"}]).content, "ok")
        sleep.assert_called_once_with(1)
        self.assertEqual(len(attempts), 2)

    def test_auth_failure_is_not_retried(self):
        error = urllib.error.HTTPError("https://example.com", 401, "unauthorized", {}, io.BytesIO(b"bad key"))
        with patch("urllib.request.urlopen", side_effect=error) as opener:
            client = OpenAICompatibleClient(self.config)
            with self.assertRaises(LLMError):
                client.chat([])
        self.assertEqual(opener.call_count, 1)
