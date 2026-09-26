"""LLM 客户端。

包含两种实现：

* :class:`OpenAICompatibleClient` —— 通过标准库 ``urllib`` 调用任意
  OpenAI 兼容的 ``/chat/completions`` 接口（OpenAI、DeepSeek、通义千问
  compatible-mode、vLLM、Ollama 等），内置超时、重试与指数退避。
* :class:`MockLLM` —— 离线规则引擎。没有 API Key 时用它驱动 Agent，
  依然完整走通「输入 → 推理 → 工具调用 → 输出」的多步循环，
  便于课堂演示、单元测试与无网络环境验收。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .analysis import SEVERITY_LABEL
from .config import AgentConfig


class LLMError(RuntimeError):
    """LLM 调用失败（网络错误、鉴权失败、响应格式异常等）。"""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None


class OpenAICompatibleClient:
    """OpenAI 兼容接口客户端。"""

    def __init__(self, config: AgentConfig, opener: Optional[Any] = None) -> None:
        self.config = config
        self._opener = opener or urllib.request.urlopen
        self.name = f"openai-compatible({config.model})"

    # ---------------------------------------------------------------- 调用
    def chat(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [self._to_api_message(message) for message in messages],
            "temperature": self.config.temperature,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        data = self._post_with_retry(payload)
        return self._parse_response(data)

    @staticmethod
    def _to_api_message(message: Dict[str, Any]) -> Dict[str, Any]:
        role = message.get("role")
        if role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.get("tool_call_id", ""),
                "content": message.get("content", ""),
            }
        if role == "assistant" and message.get("tool_calls"):
            return {
                "role": "assistant",
                "content": message.get("content") or None,
                "tool_calls": message["tool_calls"],
            }
        return {"role": role, "content": message.get("content", "")}

    def _post_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        last_error: Optional[LLMError] = None
        for attempt in range(1, self.config.max_retries + 1):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with self._opener(request, timeout=self.config.request_timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:400]
                except Exception:  # noqa: BLE001 —— 读取错误响应体失败不影响主流程
                    detail = ""
                last_error = LLMError(f"接口返回 HTTP {exc.code}：{detail or exc.reason}")
                retryable = exc.code in (408, 409, 425, 429) or exc.code >= 500
                if retryable and attempt < self.config.max_retries:
                    self._sleep(attempt)
                    continue
                raise last_error
            except json.JSONDecodeError as exc:
                raise LLMError(f"无法解析接口响应：{exc}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = LLMError(f"网络请求失败：{exc}")
                if attempt < self.config.max_retries:
                    self._sleep(attempt)
                    continue
                raise last_error
        raise last_error or LLMError("未知的调用失败")

    def _sleep(self, attempt: int) -> None:
        delay = min(self.config.retry_backoff ** (attempt - 1), 8.0)
        time.sleep(delay)

    @staticmethod
    def _parse_response(data: Dict[str, Any]) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("接口响应中缺少 choices 字段")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):  # 兼容多模态分片格式
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )

        calls: List[ToolCall] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            function = raw_call.get("function") or {}
            arguments_text = function.get("arguments")
            if isinstance(arguments_text, str):
                try:
                    arguments = json.loads(arguments_text or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments_text}
            elif isinstance(arguments_text, dict):
                arguments = dict(arguments_text)
            else:
                arguments = {}
            calls.append(
                ToolCall(
                    id=raw_call.get("id") or f"call_{index}",
                    name=str(function.get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        return LLMResponse(content=str(content).strip(), tool_calls=calls, raw=data)


class MockLLM:
    """离线规则引擎：不联网也能演示完整的 Agent 循环。

    策略：根据「用户意图 + 已经调用过的工具」决定下一步动作，
    形成一个确定性的多步计划：

    1. 代码审查 / 解释 / 测试  → ``read_file`` → ``analyze_code`` → 生成结论
    2. 运行验证                → ``run_python`` → 汇总输出
    3. 结构探查                → ``list_dir`` / ``search_in_files`` → 汇总
    """

    name = "mock(offline)"

    _FILE_RE = re.compile(
        r"([\w\-.\\/]*[\w\-.\\/]+\.(?:pyi?|jsx?|tsx?|java|c|h|cpp|hpp|cs|go|rs|rb|php|kt|swift"
        r"|sql|sh|md|txt|json|ya?ml|toml|ini|cfg|html|css))\b",
        re.IGNORECASE,
    )

    def __init__(self, config: Optional[AgentConfig] = None) -> None:
        self.config = config or AgentConfig()
        self._counter = 0

    # ---------------------------------------------------------------- 主逻辑
    def chat(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        available = {spec["function"]["name"] for spec in (tools or [])}
        user_text = self._last_user_text(messages)
        # 只统计「本轮」的工具调用与结果：历史轮次的工具结果不能复用，
        # 否则第二轮的结论会引用上一轮的文件内容。
        turn = self._current_turn(messages)
        called = [str(m.get("name")) for m in turn if m.get("role") == "tool"]
        results = self._tool_results(turn)
        intent = self._detect_intent(user_text)
        path = self._extract_path(user_text)

        def has(name: str) -> bool:
            return name in called and name in available

        # 1) 运行结果优先汇总
        if "run_python" in results:
            return self._final(self._run_summary(results["run_python"], path))

        # 2) 只读类工具失败 → 直接向用户解释原因，避免无用重试
        for name in ("read_file", "analyze_code", "list_dir", "search_in_files"):
            data = results.get(name)
            if data and not data.get("ok", True):
                return self._final(self._error_answer(name, data))

        # 3) 目录与搜索意图
        if intent == "list":
            if not has("list_dir"):
                return self._call("list_dir", {"path": path or ".", "recursive": True}, "先看一下工作区结构。")
            return self._final(self._listing_summary(results["list_dir"]))

        if intent == "search":
            if not has("search_in_files"):
                query = self._extract_query(user_text) or "TODO"
                return self._call(
                    "search_in_files",
                    {"query": query, "path": path or ".", "glob": "*.py"},
                    f"在工作区中搜索 `{query}`。",
                )
            return self._final(self._search_summary(results["search_in_files"]))

        # 4) 运行意图
        if intent == "run":
            if not path:
                return self._final(
                    "请告诉我需要运行的 Python 文件路径，例如：`运行 examples/buggy_sample.py`。"
                )
            if not has("run_python"):
                return self._call("run_python", {"path": path}, f"准备执行 {path}。")

        # 5) 需要文件内容的任务：读取 → 分析 → 输出
        if not path:
            if not has("list_dir"):
                return self._call("list_dir", {"path": ".", "recursive": True}, "还没有目标文件，先列出可分析的文件。")
            return self._final(self._ask_for_file(results["list_dir"]))

        if not has("read_file"):
            return self._call("read_file", {"path": path}, f"先读取 {path} 的源码。")

        if not has("analyze_code"):
            return self._call("analyze_code", {"path": path}, "源码已读取，接下来做静态分析。")

        read_result = results.get("read_file") or {}
        analysis = results.get("analyze_code") or {}
        if intent == "explain":
            return self._final(self._explain_answer(read_result, analysis))
        if intent == "tests":
            return self._final(self._tests_answer(read_result, analysis))
        return self._final(self._review_answer(read_result, analysis))

    # ---------------------------------------------------------- 消息解析工具
    @staticmethod
    def _last_user_text(messages: Sequence[Dict[str, Any]]) -> str:
        for message in reversed(list(messages)):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""

    @staticmethod
    def _current_turn(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """返回最后一条 user 消息之后（含该消息）的消息片段。"""

        items = list(messages)
        for index in range(len(items) - 1, -1, -1):
            if items[index].get("role") == "user":
                return items[index:]
        return items

    @staticmethod
    def _tool_results(messages: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        for message in messages:
            if message.get("role") != "tool":
                continue
            try:
                data = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                data = {"ok": False, "error": "工具返回内容不是合法 JSON"}
            if isinstance(data, dict):
                results[str(message.get("name"))] = data
        return results

    def _call(self, name: str, arguments: Dict[str, Any], thought: str = "") -> LLMResponse:
        self._counter += 1
        return LLMResponse(
            content=thought,
            tool_calls=[ToolCall(id=f"mock_{self._counter}", name=name, arguments=arguments)],
        )

    @staticmethod
    def _final(text: str) -> LLMResponse:
        return LLMResponse(content=text)

    # ------------------------------------------------------------ 意图识别
    @staticmethod
    def _detect_intent(text: str) -> str:
        lowered = text.lower()
        if any(key in text for key in ("列出", "列表", "目录", "有哪些文件", "项目结构")):
            return "list"
        if "ls " in lowered or lowered.strip() in {"ls", "dir"}:
            return "list"
        if any(key in text for key in ("搜索", "查找", "检索", "grep")):
            return "search"
        if "search" in lowered or "find " in lowered:
            return "search"
        if any(key in text for key in ("运行", "执行", "跑一下", "跑下")):
            return "run"
        if "run " in lowered or lowered.startswith("run"):
            return "run"
        if any(key in text for key in ("解释", "讲解", "说明这段", "看懂", "什么意思")):
            return "explain"
        if "explain" in lowered:
            return "explain"
        if any(key in text for key in ("单元测试", "测试用例", "生成测试", "补测试", "写测试")):
            return "tests"
        if "unit test" in lowered or "pytest" in lowered:
            return "tests"
        return "review"

    def _extract_path(self, text: str) -> Optional[str]:
        for match in self._FILE_RE.finditer(text or ""):
            candidate = match.group(1).strip("`'\"()[]，。：:；;")
            if candidate and not candidate.lower().startswith("http"):
                return candidate
        return None

    @staticmethod
    def _extract_query(text: str) -> Optional[str]:
        for key in ("搜索", "查找", "检索"):
            if key in text:
                rest = text.split(key, 1)[1].strip(" 的：:，,。\"'“”")
                return rest.split()[0] if rest else None
        quoted = re.search(r"[\"'“”](.+?)[\"'“”]", text or "")
        return quoted.group(1) if quoted else None

    # ------------------------------------------------------------ 结论生成
    @staticmethod
    def _cell(text: Any) -> str:
        return str(text).replace("|", "\\|").replace("\n", " ").strip()

    def _review_answer(self, read_result: Dict[str, Any], analysis: Dict[str, Any]) -> str:
        path = analysis.get("path") or read_result.get("path") or "目标文件"
        metrics = analysis.get("metrics") or {}
        findings = analysis.get("findings") or []
        score = analysis.get("score", 100)
        grade = analysis.get("grade", "")
        lines: List[str] = [
            f"# 代码审查报告：`{path}`",
            "",
            f"**综合评分：{score}/100（{grade}）**",
            "",
            f"- 规模：{metrics.get('total_lines', 0)} 行（代码 {metrics.get('code_lines', 0)} 行，"
            f"注释 {metrics.get('comment_lines', 0)} 行）",
            f"- 结构：{metrics.get('functions', 0)} 个函数 / {metrics.get('classes', 0)} 个类 / "
            f"{metrics.get('imports', 0)} 处导入",
            f"- 最长函数：{metrics.get('max_function_length', 0)} 行",
            "",
        ]
        if analysis.get("parse_error"):
            lines += [f"> ⚠️ 文件存在语法错误：{analysis['parse_error']}", ""]

        if not findings:
            lines += [
                "## 结论",
                "",
                "本次静态检查未发现明显问题。建议补充单元测试覆盖边界输入，并确认异常处理路径。",
            ]
            return "\n".join(lines)

        summary = analysis.get("severity_summary") or {}
        lines += [
            f"## 问题清单（共 {len(findings)} 项：高 {summary.get('high', 0)} / "
            f"中 {summary.get('medium', 0)} / 低 {summary.get('low', 0)}）",
            "",
            "| 严重度 | 规则 | 位置 | 问题 | 影响 | 建议 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for finding in findings:
            label = SEVERITY_LABEL.get(finding.get("severity", "low"), "低")
            lines.append(
                f"| {label} | `{finding.get('code', '')}` | L{finding.get('line', 0)} | "
                f"{self._cell(finding.get('title', ''))} | {self._cell(finding.get('message', ''))} | "
                f"{self._cell(finding.get('suggestion', ''))} |"
            )

        lines += ["", "## 优先修复顺序", ""]
        for index, finding in enumerate(findings[:5], 1):
            label = SEVERITY_LABEL.get(finding.get("severity", "low"), "低")
            lines.append(
                f"{index}. **[{label}] `{finding.get('code', '')}` L{finding.get('line', 0)} "
                f"{self._cell(finding.get('title', ''))}** —— {self._cell(finding.get('suggestion', ''))}"
            )

        high_risk = [f for f in findings if f.get("severity") == "high"]
        if high_risk:
            lines += [
                "",
                "## 风险提示",
                "",
                f"存在 {len(high_risk)} 个高优先级问题，涉及正确性或安全性，建议在合并代码前修复。",
            ]
        lines += [
            "",
            "> 本报告由静态分析工具基于真实代码生成，行号可直接跳转核对。",
        ]
        return "\n".join(lines)

    def _explain_answer(self, read_result: Dict[str, Any], analysis: Dict[str, Any]) -> str:
        path = analysis.get("path") or read_result.get("path") or "目标文件"
        metrics = analysis.get("metrics") or {}
        symbols = analysis.get("symbols") or {}
        lines: List[str] = [
            f"# 代码说明：`{path}`",
            "",
            f"文件共 {metrics.get('total_lines', 0)} 行，包含 "
            f"{metrics.get('classes', 0)} 个类、{metrics.get('functions', 0)} 个函数。",
            "",
            "## 结构概览",
            "",
        ]
        classes = symbols.get("classes") or []
        functions = [f for f in (symbols.get("functions") or []) if not f.get("is_method")]
        if classes:
            for item in classes:
                lines.append(f"- `class {item['name']}`（L{item['line']}）")
                for method in item.get("methods", []):
                    lines.append(f"  - 方法 `{method}()`")
        else:
            lines.append("- 该文件没有定义类。")
        if functions:
            lines.append("- 顶层函数：")
            for item in functions:
                args = ", ".join(item.get("args", []))
                lines.append(
                    f"  - `{item['name']}({args})`（L{item['line']}-L{item['end_line']}，"
                    f"{item['length']} 行）"
                )
        else:
            lines.append("- 该文件没有顶层函数，逻辑主要写在模块级语句中。")

        lines += ["", "## 阅读建议", ""]
        entry = functions[0] if functions else (classes[0] if classes else None)
        if entry:
            name = entry.get("name")
            lines.append(f"1. 先从 `{name}` 读起，它是这个文件的主要入口。")
        lines.append("2. 关注函数之间的数据流向：谁负责读取输入、谁负责处理、谁负责输出。")
        lines.append("3. 注意异常分支与边界条件，它们通常是最容易出问题的部分。")

        findings = analysis.get("findings") or []
        if findings:
            lines += ["", "## 需要注意的风险点", ""]
            for finding in findings[:3]:
                lines.append(
                    f"- L{finding.get('line', 0)} **{self._cell(finding.get('title', ''))}**："
                    f"{self._cell(finding.get('message', ''))}"
                )
        return "\n".join(lines)

    def _tests_answer(self, read_result: Dict[str, Any], analysis: Dict[str, Any]) -> str:
        path = analysis.get("path") or read_result.get("path") or "target.py"
        target_name = path.split("/")[-1]
        symbols = analysis.get("symbols") or {}
        functions = [f for f in (symbols.get("functions") or []) if not f.get("is_method")]
        classes = symbols.get("classes") or []

        lines: List[str] = [
            f"# 测试建议：`{path}`",
            "",
            "## 待覆盖目标",
            "",
            "| 符号 | 位置 | 建议覆盖的用例 |",
            "| --- | --- | --- |",
        ]
        for item in functions:
            lines.append(
                f"| `{item['name']}()` | L{item['line']} | 正常输入、边界值、异常输入 |"
            )
        for item in classes:
            lines.append(
                f"| `{item['name']}` | L{item['line']} | 构造与每个公开方法的正常 / 异常路径 |"
            )
        if not functions and not classes:
            lines.append("| （未发现可测符号） | - | 建议先重构出可测试的函数 |")

        lines += [
            "",
            "## 可直接运行的测试脚手架",
            "",
            "把下面的内容保存为与本文件同目录的 `test_" + target_name.replace(".py", "") + ".py`，然后执行 `python -m unittest`：",
            "",
            "```python",
            '"""由 Code Agent 生成的单元测试脚手架（离线模式）。"""',
            "import importlib.util",
            "import unittest",
            "from pathlib import Path",
            "",
            f'TARGET = Path(__file__).with_name("{target_name}")',
            "",
            "",
            "def _load_module():",
            "    spec = importlib.util.spec_from_file_location(TARGET.stem, TARGET)",
            "    module = importlib.util.module_from_spec(spec)",
            "    spec.loader.exec_module(module)",
            "    return module",
            "",
            "",
            "class TestTarget(unittest.TestCase):",
            "    @classmethod",
            "    def setUpClass(cls):",
            "        cls.module = _load_module()",
            "",
        ]
        if functions:
            for item in functions:
                name = re.sub(r"[^0-9A-Za-z_]", "_", item["name"])
                lines += [
                    f"    def test_{name}_normal_case(self):",
                    f'        """TODO: 为 {item["name"]}() 构造正常输入并断言返回值。"""',
                    '        self.skipTest("待补充断言")',
                    "",
                ]
        else:
            lines += [
                "    def test_module_importable(self):",
                '        """该文件没有顶层函数，至少保证可以被导入。"""',
                "        self.assertIsNotNone(self.module)",
                "",
            ]
        lines += [
            "    def test_invalid_input_should_not_crash_silently(self):",
            '        """TODO: 传入非法输入，确认抛出明确异常而不是静默失败。"""',
            '        self.skipTest("待补充断言")',
            "",
            "",
            'if __name__ == "__main__":',
            "    unittest.main()",
            "```",
            "",
            "> 脚手架只提供结构，断言需要结合业务语义补全；离线模式不会编造预期结果。",
        ]
        return "\n".join(lines)

    def _run_summary(self, result: Dict[str, Any], path: Optional[str]) -> str:
        target = result.get("path") or path or "<代码片段>"
        lines = [f"# 运行结果：`{target}`", ""]
        if result.get("timeout"):
            return "\n".join(
                lines
                + [
                    f"- 结果：**超时被终止**（超过 {result['timeout']} 秒）",
                    "",
                    "建议检查是否存在死循环或阻塞式 IO，并给外部调用加上超时。",
                ]
            )
        if result.get("error"):
            return "\n".join(
                lines
                + [
                    "- 结果：**未执行**",
                    f"- 原因：{result['error']}",
                    "",
                    "提示：如需真的执行代码，请去掉 `--no-exec` 或 `--read-only` 参数后重试。",
                ]
            )
        code = result.get("returncode")
        lines += [
            f"- 退出码：{code}（{'执行成功' if code == 0 else '执行失败'}）",
            f"- 耗时：{result.get('duration_ms', 0)} ms",
            "",
            "## 标准输出",
            "",
            "```text",
            str(result.get("stdout") or "").strip() or "(空)",
            "```",
            "",
            "## 标准错误",
            "",
            "```text",
            str(result.get("stderr") or "").strip() or "(空)",
            "```",
            "",
        ]
        if code == 0:
            lines.append("结论：程序正常结束，输出如上。")
        else:
            lines.append("结论：程序以非 0 退出码结束，请根据标准错误中的最后一条堆栈定位问题。")
        return "\n".join(lines)

    def _listing_summary(self, result: Dict[str, Any]) -> str:
        entries = result.get("entries") or []
        lines = [
            f"# 工作区结构：`{result.get('root', '.')}`",
            "",
            f"共 {result.get('count', 0)} 项{'（已截断）' if result.get('truncated') else ''}：",
            "",
        ]
        for entry in entries[:40]:
            mark = "目录" if entry.get("type") == "dir" else "文件"
            lines.append(f"- [{mark}] `{entry.get('path')}`")
        if len(entries) > 40:
            lines.append(f"- ...（其余 {len(entries) - 40} 项省略）")
        lines += [
            "",
            "请告诉我需要审查或解释哪个文件，例如：`审查 examples/buggy_sample.py`。",
        ]
        return "\n".join(lines)

    def _search_summary(self, result: Dict[str, Any]) -> str:
        matches = result.get("matches") or []
        lines = [
            f"# 搜索结果：`{result.get('query')}`",
            "",
            f"在 {result.get('scanned_files', 0)} 个文件中命中 {result.get('count', 0)} 处"
            f"{'（已截断）' if result.get('truncated') else ''}。",
            "",
        ]
        for item in matches[:40]:
            lines.append(f"- `{item.get('path')}:{item.get('line')}` — {item.get('text')}")
        if not matches:
            lines.append("- 没有命中结果，可以尝试换关键字或放宽 glob 过滤。")
        return "\n".join(lines)

    def _ask_for_file(self, result: Dict[str, Any]) -> str:
        files = [e["path"] for e in (result.get("entries") or []) if e.get("type") == "file"]
        candidates = [path for path in files if path.endswith((".py", ".js", ".ts", ".java", ".go"))][:8]
        lines = [
            "我还没有拿到目标文件，请指定要处理的文件路径，例如：`审查 examples/buggy_sample.py`。",
            "",
            "当前目录下可以分析的文件：",
            "",
        ]
        for path in candidates or files[:8]:
            lines.append(f"- `{path}`")
        if not (candidates or files):
            lines.append("- （当前目录没有可分析的文件）")
        return "\n".join(lines)

    @staticmethod
    def _error_answer(tool: str, data: Dict[str, Any]) -> str:
        hints = {
            "read_file": "请确认文件路径拼写是否正确，或先用 `列出目录` 查看项目结构。",
            "analyze_code": "请确认目标文件存在且是文本文件。",
            "list_dir": "请确认目录路径存在。",
            "search_in_files": "请换一个关键字，或缩小搜索目录。",
        }
        return "\n".join(
            [
                f"工具 `{tool}` 执行失败：{data.get('error', '未知错误')}",
                "",
                hints.get(tool, "请调整参数后重试。"),
            ]
        )


def create_client(config: AgentConfig) -> Any:
    """按配置创建 LLM 客户端。"""

    if config.active_provider == "mock":
        return MockLLM(config)
    return OpenAICompatibleClient(config)
