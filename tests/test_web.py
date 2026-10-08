"""Web 界面的单元测试：用标准库 http.client 打本地服务，验证页面与 API。"""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from unittest.mock import patch
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

from agent.config import AgentConfig
from agent.web import _Handler
from tests import make_workspace, remove_workspace


class WebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_workspace("web-")
        (self.root / "a.py").write_text(
            '"""样例模块。"""\n\n\ndef add(left, right):\n    """返回两数之和。"""\n\n    return left + right\n',
            encoding="utf-8",
        )
        config = AgentConfig(
            provider="mock",
            workspace=self.root,
            session_dir=self.root / ".agent_sessions",
            max_iterations=6,
        )
        _Handler.config = config
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        remove_workspace(self.root)

    def _request(self, method: str, path: str, body: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read().decode("utf-8", errors="replace")
        conn.close()
        return response.status, data

    def test_index_page_is_served(self) -> None:
        status, body = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("代码助手 Agent", body)
        self.assertIn("/static/workbench.js", body)

    def test_index_page_exposes_all_controls(self) -> None:
        """界面上该有的控件都要在：模型来源、任务模式、会话、上传、输入框、Markdown 渲染。"""

        status, body = self._request("GET", "/")
        self.assertEqual(status, 200)
        for needle in (
            'id="provider"',
            'id="mode"',
            'id="session"',
            'id="file"',
            'id="message"',
            'id="send"',
            "/static/workbench.css",
            'id="scan-project"',
            'id="refresh-git"',
        ):
            self.assertIn(needle, body, f"页面缺少 {needle}")

    def test_static_assets_are_served_and_traversal_is_rejected(self) -> None:
        for path, expected in (("/static/workbench.js", "renderMarkdown"), ("/static/workbench.css", ".sidebar")):
            status, body = self._request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn(expected, body)
        self.assertEqual(self._request("GET", "/static/../config.py")[0], 404)

    def test_status_reports_provider_and_modes(self) -> None:
        status, body = self._request("GET", "/api/status")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["provider"], "mock")
        names = [item["name"] for item in payload["modes"]]
        self.assertIn("review", names)
        self.assertIn("refactor", names)

    def test_chat_endpoint_runs_agent_and_returns_steps(self) -> None:
        status, body = self._request(
            "POST",
            "/api/chat",
            json.dumps({"message": "审查 a.py", "mode": "review", "session": "s1"}),
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["stopped"], "final")
        self.assertEqual(payload["mode"], "review")
        self.assertIn("代码审查报告", payload["answer"])
        tool_steps = [step for step in payload["steps"] if step["kind"] == "tool"]
        self.assertEqual([step["title"] for step in tool_steps[:2]], ["调用工具 read_file", "调用工具 analyze_code"])

    def test_chat_endpoint_rejects_bad_json(self) -> None:
        status, _ = self._request("POST", "/api/chat", "{not json")
        self.assertEqual(status, 400)

    def test_upload_saves_file_and_agent_can_review_it(self) -> None:
        status, body = self._request(
            "POST",
            "/api/upload",
            json.dumps({"filename": "calc.py", "content": "def add(a, b):\n    return a + b\n"}),
        )
        self.assertEqual(status, 200)
        uploaded = json.loads(body)
        self.assertEqual(uploaded["path"], "uploads/calc.py")
        self.assertTrue((self.root / "uploads" / "calc.py").is_file())

        status2, body2 = self._request(
            "POST",
            "/api/chat",
            json.dumps({"message": "审查 uploads/calc.py", "mode": "review", "session": "s2"}),
        )
        self.assertEqual(status2, 200)
        self.assertIn("代码审查报告", json.loads(body2)["answer"])

    def test_upload_strips_path_traversal(self) -> None:
        status, body = self._request(
            "POST",
            "/api/upload",
            json.dumps({"filename": "../evil.py", "content": "x = 1\n"}),
        )
        self.assertEqual(status, 200)
        path = json.loads(body)["path"]
        self.assertTrue(path.startswith("uploads/"))
        self.assertNotIn("..", path)
        self.assertTrue((self.root / path).is_file())

    def test_upload_rejects_empty_filename(self) -> None:
        status, _ = self._request(
            "POST", "/api/upload", json.dumps({"filename": "  ", "content": "x\n"})
        )
        self.assertEqual(status, 400)

    def test_upload_requires_content(self) -> None:
        status, _ = self._request("POST", "/api/upload", json.dumps({"filename": "a.py"}))
        self.assertEqual(status, 400)

    def test_provider_override_switches_to_offline(self) -> None:
        """默认配置是在线且指向不可达地址；请求里指定 provider=mock 时应能离线跑通。"""

        _Handler.config = AgentConfig(
            provider="openai",
            api_key="sk-dummy",
            base_url="http://127.0.0.1:9/v1",
            workspace=self.root,
            session_dir=self.root / ".agent_sessions",
            max_retries=1,
        )
        status, body = self._request(
            "POST",
            "/api/chat",
            json.dumps({"message": "审查 a.py", "mode": "review", "session": "s3", "provider": "mock"}),
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["stopped"], "final")
        self.assertIn("代码审查报告", payload["answer"])

    def test_auto_preserves_explicit_offline_server(self) -> None:
        _Handler.config = replace(_Handler.config, api_key="dummy-local-test")
        with patch("agent.llm.OpenAICompatibleClient.chat", side_effect=AssertionError("must stay offline")):
            status, body = self._request("POST", "/api/chat", json.dumps({"message": "审查 a.py", "provider": "auto"}))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["stopped"], "final")

    def test_all_six_modes_work_through_web_api(self) -> None:
        """五个方向 + 通用问答，逐个通过 Web 接口跑一遍，确保功能都可用。"""

        cases = [
            ("review", "审查 a.py", "代码审查报告"),
            ("explain", "解释 a.py", "代码说明"),
            ("test", "为 a.py 生成单元测试", "测试建议"),
            ("refactor", "重构 a.py", "重构建议"),
            ("generate", "写一个 add 函数", "代码生成"),
            ("ask", "列出目录", "工作区结构"),
        ]
        for mode, message, expected in cases:
            with self.subTest(mode=mode):
                status, body = self._request(
                    "POST",
                    "/api/chat",
                    json.dumps(
                        {
                            "message": message,
                            "mode": mode,
                            "session": "m-" + mode,
                            "provider": "mock",
                        }
                    ),
                )
                self.assertEqual(status, 200)
                payload = json.loads(body)
                self.assertEqual(payload["mode"], mode)
                self.assertEqual(payload["stopped"], "final", payload["answer"][:150])
                self.assertIn(expected, payload["answer"])

    def test_chat_stream_emits_steps_then_done(self) -> None:
        """SSE 接口：先推工具步骤，最后推 done 事件，并以连接关闭作为结束。"""

        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        payload = json.dumps(
            {"message": "审查 a.py", "mode": "review", "session": "sse", "provider": "mock"}
        )
        conn.request("POST", "/api/chat/stream", body=payload, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("text/event-stream"))
        body = response.read().decode("utf-8", errors="replace")
        conn.close()
        self.assertIn("read_file", body)
        self.assertIn("event: done", body)
        self.assertIn("代码审查报告", body)
        final = json.loads(body.split("event: done\ndata: ")[1].strip())
        self.assertEqual(final["report"]["provider"], "mock")
        self.assertTrue(final["report"]["steps"])

    def test_get_cannot_start_a_task(self) -> None:
        self.assertEqual(self._request("GET", "/api/chat/stream?message=hello")[0], 405)

    def test_invalid_chat_fields(self) -> None:
        for payload in ({}, {"message": []}, {"message": "hi", "session": []}):
            self.assertEqual(self._request("POST", "/api/chat", json.dumps(payload))[0], 400)

    def test_duplicate_upload_preserves_original(self) -> None:
        paths = []
        for content in ("first", "second"):
            status, body = self._request("POST", "/api/upload", json.dumps({"filename": "same.py", "content": content}))
            self.assertEqual(status, 200)
            paths.append(json.loads(body)["path"])
        self.assertNotEqual(*paths)
        self.assertEqual((self.root / paths[0]).read_text(), "first")

    def test_read_only_upload_is_rejected(self) -> None:
        _Handler.config = replace(_Handler.config, read_only=True)
        status, _ = self._request("POST", "/api/upload", json.dumps({"filename": "a.py", "content": "x"}))
        self.assertEqual(status, 403)
        self.assertFalse((self.root / "uploads").exists())

    def test_unexpected_error_remains_json(self) -> None:
        with patch("agent.web._make_agent", side_effect=OSError("failure")), self.assertLogs(level="ERROR"):
            status, body = self._request("POST", "/api/chat", json.dumps({"message": "hello"}))
        self.assertEqual(status, 500)
        self.assertIn("error", json.loads(body))

    def test_oversized_or_invalid_length_is_rejected_before_read(self) -> None:
        for value, expected in (("-1", 400), ("oops", 400), ("99999999", 413)):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("POST", "/api/chat", headers={"Content-Length": value})
            response = conn.getresponse()
            self.assertEqual(response.status, expected)
            response.read()
            conn.close()

    def test_cross_site_post_is_rejected(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/chat", body='{"message":"hi"}', headers={"Origin": "https://example.com"})
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.close()

    def test_unknown_route_returns_404(self) -> None:
        status, _ = self._request("GET", "/nope")
        self.assertEqual(status, 404)

    def test_project_scan_and_file_preview_endpoints(self) -> None:
        status, body = self._request("GET", "/api/project")
        self.assertEqual(status, 200)
        self.assertIn("a.py", [row["path"] for row in json.loads(body)["files"]])
        status, body = self._request("POST", "/api/project/scan", "{}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["files_scanned"], 1)
        status, body = self._request("GET", "/api/file?path=a.py")
        self.assertEqual(status, 200)
        self.assertIn("def add", json.loads(body)["content"])
        self.assertEqual(self._request("GET", "/api/file?path=.env")[0], 400)
        self.assertEqual(self._request("GET", "/api/file?path=../main.py")[0], 400)

    def test_history_restores_user_and_final_messages(self) -> None:
        self._request("POST", "/api/chat", json.dumps({"message": "审查 a.py", "session": "restore"}))
        status, body = self._request("GET", "/api/history?session=restore")
        self.assertEqual(status, 200)
        messages = json.loads(body)["messages"]
        self.assertEqual([row["role"] for row in messages], ["user", "assistant"])
        self.assertIn("代码审查报告", messages[-1]["content"])
        _, body = self._request("GET", "/api/sessions")
        self.assertIn("restore", [row["session_id"] for row in json.loads(body)["sessions"]])

    def test_project_test_endpoint_honors_no_exec(self) -> None:
        _Handler.config = replace(_Handler.config, allow_exec=False)
        with patch("agent.tools.subprocess.run") as runner:
            status, body = self._request("POST", "/api/project/tests", '{"path":"."}')
        self.assertEqual(status, 400)
        self.assertIn("禁用", json.loads(body)["error"])
        runner.assert_not_called()

    def test_concurrent_same_session_preserves_both_turns(self) -> None:
        def request(_):
            return self._request("POST", "/api/chat", json.dumps({"message": "审查 a.py", "session": "shared"}))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(request, range(2)))
        self.assertTrue(all(status == 200 for status, body in results))
        _, body = self._request("GET", "/api/history?session=shared")
        self.assertEqual([row["role"] for row in json.loads(body)["messages"]], ["user", "assistant", "user", "assistant"])


if __name__ == "__main__":
    unittest.main()
