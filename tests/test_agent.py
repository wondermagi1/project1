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

    def _make_agent(
        self, client: Any = None, on_event: Any = None, mode: str = "auto"
    ) -> CodeAgent:
        memory = ConversationMemory(system_prompt="sys", session_dir=self.root, autosave=False)
        return CodeAgent(
            config=self.config,
            client=client or MockLLM(self.config),
            registry=self.registry,
            memory=memory,
            on_event=on_event,
            mode=mode,
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
        # 迭代上限后会再尝试一次「不带工具的强制总结」，因此调用次数可能多 1 次
        self.assertGreaterEqual(client.calls, self.config.max_iterations)

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

    # ------------------------------------------------- 五个方向的模式化行为
    def test_result_reports_effective_mode(self) -> None:
        agent = self._make_agent()
        self.assertEqual(agent.run("审查 demo.py").mode, "review")
        self.assertEqual(agent.run("解释 demo.py").mode, "explain")
        self.assertEqual(agent.run("写一个 quicksort 函数").mode, "generate")

    def test_explicit_mode_is_used_even_without_keywords(self) -> None:
        agent = self._make_agent(mode="refactor")
        result = agent.run("看看 demo.py")
        self.assertEqual(result.mode, "refactor")
        self.assertIn("重构建议", result.answer)

    def test_set_mode_updates_system_prompt(self) -> None:
        agent = self._make_agent()
        agent.set_mode("test")
        system_messages = [m for m in agent.memory.messages() if m["role"] == "system"]
        self.assertEqual(len(system_messages), 1)
        self.assertIn("测试生成", system_messages[0]["content"])

    def test_test_mode_writes_and_runs_test_file(self) -> None:
        agent = self._make_agent(mode="test")
        result = agent.run("为 demo.py 生成单元测试")
        tool_names = [step.title.replace("调用工具 ", "") for step in result.steps if step.kind == "tool"]
        self.assertIn("write_file", tool_names)
        self.assertIn("run_tests", tool_names)
        self.assertTrue((self.root / "test_demo.py").is_file(), "应当真的写出测试文件")
        self.assertIn("已生成的测试文件", result.answer)
        self.assertIn("实际运行结果", result.answer)
        self.assertIn("unittest", result.answer)

    def test_refactor_mode_writes_copy_and_diff(self) -> None:
        (self.root / "legacy.py").write_text(
            '"""遗留代码。"""\n\n\ndef normalize(value=None):\n    """规范化。"""\n\n    if value == None:\n        return ""\n    return value\n',
            encoding="utf-8",
        )
        agent = self._make_agent(mode="refactor")
        result = agent.run("重构 legacy.py")
        tool_names = [step.title.replace("调用工具 ", "") for step in result.steps if step.kind == "tool"]
        self.assertIn("diff_files", tool_names)
        self.assertTrue((self.root / "legacy_refactored.py").is_file())
        self.assertIn("is None", (self.root / "legacy_refactored.py").read_text(encoding="utf-8"))
        self.assertIn("坏味道清单", result.answer)
        self.assertIn("已安全落地的重构", result.answer)

    def test_generate_mode_writes_skeleton_and_verifies(self) -> None:
        agent = self._make_agent(mode="generate")
        result = agent.run("写一个 quicksort 函数")
        tool_names = [step.title.replace("调用工具 ", "") for step in result.steps if step.kind == "tool"]
        self.assertIn("write_file", tool_names)
        self.assertIn("run_python", tool_names)
        generated = self.root / "generated" / "quicksort.py"
        self.assertTrue(generated.is_file(), "骨架文件应当写入 generated/ 目录")
        self.assertIn("def quicksort", generated.read_text(encoding="utf-8"))
        self.assertIn("代码生成", result.answer)

    def test_explain_mode_proposes_docstrings(self) -> None:
        agent = self._make_agent(mode="explain")
        result = agent.run("解释 demo.py")
        tool_names = [step.title.replace("调用工具 ", "") for step in result.steps if step.kind == "tool"]
        self.assertIn("diff_files", tool_names)
        self.assertIn("建议补充的注释", result.answer)

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
