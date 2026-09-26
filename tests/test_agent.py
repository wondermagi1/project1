"""Agent 主循环的单元测试（使用伪客户端，不依赖网络）。"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from agent.agent import CodeAgent
from agent.config import AgentConfig
from agent.llm import LLMError, LLMResponse, MockLLM, ToolCall
from agent.memory import ConversationMemory
from agent.tools import build_default_registry
from tests import make_workspace, remove_workspace


class _AlwaysToolClient:
    """始终请求调用工具，用于验证最大迭代次数保护。"""

    def __init__(self, tool: str = "list_dir") -> None:
        self.tool = tool
        self.calls = 0

    def chat(self, messages: Sequence[Dict[str, Any]], tools: Optional[Any] = None) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content="继续调用工具",
            tool_calls=[ToolCall(id=f"c{self.calls}", name=self.tool, arguments={"path": "."})],
        )


class _FailingClient:
    def chat(self, messages: Sequence[Dict[str, Any]], tools: Optional[Any] = None) -> LLMResponse:
        raise LLMError("接口返回 HTTP 401：invalid api key")


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_workspace("agent-")
        (self.root / "demo.py").write_text(
            textwrap.dedent(
                '''
                """演示模块。"""


                def risky(value=[]):
                    value.append(1)
                    return value
                '''
            ).lstrip(),
            encoding="utf-8",
        )
        self.config = AgentConfig(workspace=self.root, provider="mock")
        self.registry = build_default_registry(self.config)

    def tearDown(self) -> None:
        remove_workspace(self.root)

    def _make_agent(self, client: Any = None, on_event: Any = None) -> CodeAgent:
        memory = ConversationMemory(system_prompt="sys", session_dir=self.root, autosave=False)
        return CodeAgent(
            config=self.config,
            client=client or MockLLM(self.config),
            registry=self.registry,
            memory=memory,
            on_event=on_event,
        )

    def test_mock_review_runs_full_tool_loop(self) -> None:
        agent = self._make_agent()
        result = agent.run("审查 demo.py")
        self.assertEqual(result.stopped, "final")
        self.assertIn("代码审查报告", result.answer)
        self.assertIn("PY006", " ".join(step.title for step in result.steps) + result.answer)
        tool_names = [
            step.title.replace("调用工具 ", "") for step in result.steps if step.kind == "tool"
        ]
        self.assertEqual(tool_names[:2], ["read_file", "analyze_code"])
        self.assertEqual(result.iterations, 3)

    def test_mock_explain_intent(self) -> None:
        agent = self._make_agent()
        result = agent.run("解释 demo.py 的逻辑")
        self.assertIn("代码说明", result.answer)
        self.assertIn("risky", result.answer)

    def test_mock_tests_intent(self) -> None:
        agent = self._make_agent()
        result = agent.run("为 demo.py 生成单元测试")
        self.assertIn("测试建议", result.answer)
        self.assertIn("unittest", result.answer)

    def test_missing_file_is_reported_without_crashing(self) -> None:
        agent = self._make_agent()
        result = agent.run("审查 nope.py")
        self.assertEqual(result.stopped, "final")
        self.assertIn("执行失败", result.answer)

    def test_listing_when_no_file_given(self) -> None:
        agent = self._make_agent()
        result = agent.run("帮我看看这个项目")
        self.assertIn("demo.py", result.answer)

    def test_max_iterations_guard(self) -> None:
        client = _AlwaysToolClient()
        agent = self._make_agent(client=client)
        result = agent.run("一直调用工具")
        self.assertEqual(result.stopped, "max_iterations")
        self.assertEqual(result.iterations, self.config.max_iterations)
        self.assertEqual(client.calls, self.config.max_iterations)

    def test_llm_error_is_handled_with_hint(self) -> None:
        agent = self._make_agent(client=_FailingClient())
        result = agent.run("审查 demo.py")
        self.assertEqual(result.stopped, "error")
        self.assertIn("调用大模型失败", result.answer)
        self.assertIn("API Key", result.answer)

    def test_memory_keeps_context_between_runs(self) -> None:
        agent = self._make_agent()
        agent.run("审查 demo.py")
        agent.run("再解释一下 demo.py")
        self.assertEqual(agent.memory.turns(), 2)
        roles = [message["role"] for message in agent.memory.messages()]
        self.assertIn("tool", roles)

    def test_tool_results_are_scoped_to_current_turn(self) -> None:
        (self.root / "second.py").write_text(
            '"""第二个模块。"""\n\n\ndef helper(value):\n    """辅助。"""\n\n    return value\n',
            encoding="utf-8",
        )
        agent = self._make_agent()
        agent.run("审查 demo.py")
        result = agent.run("审查 second.py")
        self.assertIn("second.py", result.answer)
        self.assertNotIn("demo.py", result.answer)

    def test_events_are_emitted_for_observation(self) -> None:
        events = []
        agent = self._make_agent(on_event=events.append)
        agent.run("审查 demo.py")
        kinds = [event.kind for event in events]
        self.assertIn("tool", kinds)
        self.assertTrue(any(event.title.startswith("调用工具") for event in events))

    def test_empty_input_returns_error(self) -> None:
        agent = self._make_agent()
        result = agent.run("   ")
        self.assertEqual(result.stopped, "error")

    def test_run_intent_reports_disabled_execution(self) -> None:
        config = AgentConfig(workspace=self.root, provider="mock", allow_exec=False)
        agent = CodeAgent(
            config=config,
            client=MockLLM(config),
            registry=build_default_registry(config),
            memory=ConversationMemory(system_prompt="sys", session_dir=self.root, autosave=False),
        )
        result = agent.run("运行 demo.py")
        self.assertIn("未执行", result.answer)
        self.assertIn("禁用", result.answer)


if __name__ == "__main__":
    unittest.main()
