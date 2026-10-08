"""LLM 客户端。

包含两种实现：

* :class:`OpenAICompatibleClient` —— 通过标准库 ``urllib`` 调用任意
  OpenAI 兼容的 ``/chat/completions`` 接口（OpenAI、DeepSeek、通义千问
  compatible-mode、vLLM、Ollama 等），内置超时、重试与指数退避。
* :class:`MockLLM` —— 离线规则引擎。没有 API Key 时用它驱动 Agent，
  依然完整走通「输入 → 推理 → 工具调用 → 输出」的多步循环，
  便于本地试用、单元测试与无网络环境验证。
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
from .modes import detect_mode, extract_mode_marker, strip_mode_marker
from .project import scan_markdown


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
        if not isinstance(data, dict):
            raise LLMError("接口响应必须是 JSON 对象")
        choices = data.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMError("接口响应中缺少 choices 字段")
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            raise LLMError("接口响应中的 message 格式错误")
        content = message.get("content") or ""
        if isinstance(content, list):  # 兼容多模态分片格式
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )

        calls: List[ToolCall] = []
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise LLMError("接口响应中的 tool_calls 必须是数组")
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                raise LLMError("接口返回了无效的工具调用")
            function = raw_call.get("function") or {}
            if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"]:
                raise LLMError("接口返回的工具调用缺少有效函数名")
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
        raw_user_text = self._last_user_text(messages)
        # 去掉了 [mode:xxx] 标记的纯需求文本，用于抽取路径、需求描述与文件名
        user_text = strip_mode_marker(raw_user_text)
        # 只统计「本轮」的工具调用与结果：历史轮次的工具结果不能复用，
        # 否则第二轮的结论会引用上一轮的文件内容。
        turn = self._current_turn(messages)
        called = [str(m.get("name")) for m in turn if m.get("role") == "tool"]
        results = self._tool_results(turn)
        intent = self._detect_intent(raw_user_text)
        path = self._extract_path(user_text)

        def has(name: str) -> bool:
            return name in called and name in available

        if any(word in user_text.lower() for word in ("项目扫描", "扫描项目", "审查整个项目", "project scan", "改动扫描")):
            if "project_scan" not in results:
                return self._call("project_scan", {"changed_only": "改动" in user_text}, "读取项目文件并汇总质量问题。")
            return self._final(scan_markdown(results["project_scan"]))
        if any(word in user_text.lower() for word in ("git", "提交前审查", "审查改动")):
            if "git_diff" not in results:
                return self._call("git_diff", {}, "读取暂存区与工作区改动。")
            result = results["git_diff"]
            if not result.get("ok"):
                return self._final("Git 检查失败：" + result.get("error", "未知错误"))
            return self._final(f"# Git 改动概览\n\n分支：{result['branch']}，{result['changed_count']} 个项目文件有改动。\n\n```diff\n{result['diff']}\n```\n\n{result['note']}\n\n离线模式仅呈现差异证据，不提供业务语义审查。")

        # 1) 「运行」意图：拿到执行结果就直接汇总
        if intent == "run" and "run_python" in results:
            return self._final(self._run_summary(results["run_python"], path))

        # 2) 只读类工具失败 → 直接向用户解释原因，避免无用重试
        for name in (
            "read_file",
            "analyze_code",
            "list_dir",
            "search_in_files",
            "write_file",
            "run_tests",
            "diff_files",
        ):
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

        # 5) 代码生成意图：离线模式产出可运行骨架，并用 run_python 验证
        if intent == "generate":
            if not has("write_file"):
                target = self._skeleton_path(user_text, path)
                return self._call(
                    "write_file",
                    {"path": target, "content": self._skeleton(user_text)},
                    "离线模式：先生成可运行的骨架文件，再验证能否正常加载。",
                )
            if not has("run_python"):
                written = (results.get("write_file") or {}).get("path")
                if written:
                    return self._call("run_python", {"path": written}, "验证生成的代码能否正常运行。")
            return self._final(
                self._generate_answer(
                    results.get("write_file") or {}, results.get("run_python"), user_text, path
                )
            )

        # 6) 需要文件内容的任务：读取 → 分析 → 输出
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
            if not has("diff_files"):
                annotated, count = self._add_docstring_stubs(
                    self._raw_source(read_result), analysis
                )
                if count:
                    return self._call(
                        "diff_files",
                        {"left": path, "content": annotated},
                        f"为 {count} 个缺少文档的公开函数生成注释建议。",
                    )
            return self._final(
                self._explain_answer(read_result, analysis, results.get("diff_files"))
            )

        if intent == "test":
            if not has("write_file"):
                guess = self._test_file_path(path)
                return self._call(
                    "write_file",
                    {
                        "path": guess,
                        "content": self._build_test_scaffold(path, analysis),
                        # 测试文件是可重复生成的产物，覆盖旧版本不会造成损失
                        "overwrite": True,
                    },
                    f"生成测试文件 {guess}。",
                )
            if not has("run_tests"):
                written = (results.get("write_file") or {}).get("path") or self._test_file_path(path)
                return self._call("run_tests", {"path": written}, "实际运行生成的测试，确认能否通过。")
            return self._final(
                self._tests_answer(
                    read_result,
                    analysis,
                    results.get("write_file"),
                    results.get("run_tests"),
                    path,
                )
            )

        if intent == "refactor":
            if not has("write_file"):
                refactored, applied = self._safe_refactor(self._raw_source(read_result))
                target = self._refactor_path(path)
                return self._call(
                    "write_file",
                    {"path": target, "content": refactored, "overwrite": True},
                    f"先落地可机械安全替换的部分（{len(applied)} 处改动）。",
                )
            if not has("diff_files"):
                target = (results.get("write_file") or {}).get("path") or self._refactor_path(path)
                return self._call("diff_files", {"left": path, "right": target}, "对比重构前后的差异。")
            return self._final(
                self._refactor_answer(
                    read_result,
                    analysis,
                    results.get("write_file"),
                    results.get("diff_files"),
                    path,
                )
            )

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
        """识别意图：字面指令（列出 / 搜索 / 运行）优先，其次才是显式模式与自动识别。

        这样即使用户把模式固定成「通用问答」，说「列出目录」也会真的去列目录。
        """

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
        marker = extract_mode_marker(text or "")
        if marker:
            return marker
        return detect_mode(text)

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

    def _explain_answer(
        self,
        read_result: Dict[str, Any],
        analysis: Dict[str, Any],
        diff_result: Optional[Dict[str, Any]] = None,
    ) -> str:
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

        if diff_result and diff_result.get("changed"):
            lines += [
                "",
                "## 建议补充的注释（diff）",
                "",
                "```diff",
                str(diff_result.get("diff") or "")[:4000],
                "```",
                "",
                f"- 变更规模：新增 {diff_result.get('added_lines', 0)} 行，"
                f"删除 {diff_result.get('removed_lines', 0)} 行",
                "- 上面的 docstring 只是骨架，措辞需要结合业务语义补全。",
            ]
        return "\n".join(lines)

    # -------------------------------------------------- 文件路径与源码转换
    @staticmethod
    def _raw_source(read_result: Dict[str, Any]) -> str:
        """把 read_file 返回的「带行号内容」还原成原始源码。"""

        rows: List[str] = []
        for line in str(read_result.get("content") or "").splitlines():
            match = re.match(r"^\s*\d+ \| ?(.*)$", line)
            rows.append(match.group(1) if match else line)
        return ("\n".join(rows) + "\n") if rows else ""

    @staticmethod
    def _sibling_path(path: str, new_name: str) -> str:
        cleaned = str(path).replace("\\", "/")
        parts = cleaned.rsplit("/", 1)
        return f"{parts[0]}/{new_name}" if len(parts) == 2 else new_name

    @classmethod
    def _test_file_path(cls, path: str) -> str:
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        stem = name[:-3] if name.endswith(".py") else name
        return cls._sibling_path(path, f"test_{stem}.py")

    @classmethod
    def _refactor_path(cls, path: str) -> str:
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        if name.endswith(".py"):
            return cls._sibling_path(path, f"{name[:-3]}_refactored.py")
        return cls._sibling_path(path, f"{name}_refactored")

    @staticmethod
    def _skeleton_name(description: str) -> str:
        skip = {"def", "class", "todo", "api", "the", "and", "for", "with", "function", "print"}
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", description or ""):
            if token.lower() in skip or not token.isalpha():
                continue
            cleaned = re.sub(r"[^a-z0-9_]", "", token.lower())
            if cleaned:
                return cleaned
        return "generated_function"

    @classmethod
    def _skeleton_path(cls, user_text: str, path: Optional[str]) -> str:
        return path or f"generated/{cls._skeleton_name(user_text)}.py"

    @classmethod
    def _skeleton(cls, description: str) -> str:
        name = cls._skeleton_name(description)
        safe = re.sub(r"\s+", " ", (description or "").strip())[:60].replace('"', "'")
        return (
            '"""由 Code Agent 生成的代码骨架（离线模式）。\n'
            "\n"
            f"需求：{safe}\n"
            "说明：离线规则引擎只能产出可运行骨架；配置 API Key 后由大模型补全实现。\n"
            '"""\n'
            "from __future__ import annotations\n"
            "\n"
            "\n"
            f"def {name}(*args, **kwargs):\n"
            f'    """TODO: 实现「{safe}」。\n'
            "\n"
            "    请补充：参数含义、返回值、异常与边界条件。\n"
            '    """\n'
            '    raise NotImplementedError("待实现：接入真实大模型后会自动补全逻辑")\n'
            "\n"
            "\n"
            'if __name__ == "__main__":\n'
            '    print("骨架文件已生成，逻辑待补全。")\n'
        )

    # -------------------------------------------------------- 机械安全重构
    @staticmethod
    def _safe_refactor(source: str) -> tuple:
        """只做语义等价、可机械验证的替换，其余留给人工评审。"""

        applied: List[str] = []
        rows: List[str] = []
        for index, line in enumerate(source.splitlines(), 1):
            new_line = line
            if re.search(r"==\s*None\b", new_line):
                new_line = re.sub(r"==\s*None\b", "is None", new_line)
                applied.append(f"L{index}: == None → is None")
            if re.search(r"!=\s*None\b", new_line):
                new_line = re.sub(r"!=\s*None\b", "is not None", new_line)
                applied.append(f"L{index}: != None → is not None")
            rows.append(new_line)
        return (("\n".join(rows) + "\n") if rows else ""), applied

    @staticmethod
    def _add_docstring_stubs(source: str, analysis: Dict[str, Any]) -> tuple:
        """为缺少文档的公开函数插入 docstring 骨架（只处理单行签名）。"""

        targets = {
            item["line"]: item
            for item in (analysis.get("symbols") or {}).get("functions", [])
            if item.get("is_public") and not item.get("has_docstring")
        }
        if not targets:
            return source, 0

        rows: List[str] = []
        count = 0
        for index, line in enumerate(source.splitlines(), 1):
            rows.append(line)
            item = targets.get(index)
            if not item or not line.rstrip().endswith(":"):
                continue
            indent = line[: len(line) - len(line.lstrip())] + "    "
            args = ", ".join(item.get("args", [])) or "无"
            rows.append(f'{indent}"""{item["name"]}：TODO 说明职责（参数：{args}；返回值：TODO）。"""')
            count += 1
        text = ("\n".join(rows) + "\n") if source.endswith("\n") else "\n".join(rows)
        return text, count

    # ------------------------------------------------------------ 测试文件
    def _build_test_scaffold(self, target_display: str, analysis: Dict[str, Any]) -> str:
        """生成可直接运行的 unittest 测试文件内容。"""

        target_name = str(target_display).replace("\\", "/").rsplit("/", 1)[-1]
        symbols = analysis.get("symbols") or {}
        functions = [f for f in (symbols.get("functions") or []) if not f.get("is_method")]
        classes = symbols.get("classes") or []
        names = [item["name"] for item in functions] + [item["name"] for item in classes]

        lines: List[str] = [
            '"""由 Code Agent 生成的单元测试脚手架（离线模式）。',
            "",
            f"被测文件：{target_display}",
            "说明：导入检查与符号存在性会自动通过；带 skipTest 的用例需结合业务语义补断言。",
            '"""',
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
            "    def test_module_importable(self):",
            '        """模块可以被正常导入（语法与顶层逻辑没有立刻崩溃）。"""',
            "        self.assertIsNotNone(self.module)",
            "",
        ]
        if names:
            lines += [
                "    def test_public_symbols_exist(self):",
                '        """公开函数 / 类都存在，避免改名后测试静默失效。"""',
                f"        for name in {names!r}:",
                '            self.assertTrue(hasattr(self.module, name), f"缺少符号 {name}")',
                "",
            ]
        for item in functions:
            name = re.sub(r"[^0-9A-Za-z_]", "_", item["name"])
            lines += [
                f"    def test_{name}_normal_case(self):",
                f'        """TODO: 为 {item["name"]}() 构造正常输入并断言返回值。"""',
                '        self.skipTest("待补充断言")',
                "",
                f"    def test_{name}_edge_cases(self):",
                f'        """TODO: 覆盖 {item["name"]}() 的边界与异常输入。"""',
                '        self.skipTest("待补充断言")',
                "",
            ]
        for item in classes:
            name = re.sub(r"[^0-9A-Za-z_]", "_", item["name"])
            lines += [
                f"    def test_{name}_usage(self):",
                f'        """TODO: 构造 {item["name"]} 并验证其公开方法。"""',
                '        self.skipTest("待补充断言")',
                "",
            ]
        lines += ["", 'if __name__ == "__main__":', "    unittest.main()"]
        return "\n".join(lines) + "\n"

    def _tests_answer(
        self,
        read_result: Dict[str, Any],
        analysis: Dict[str, Any],
        write_result: Optional[Dict[str, Any]] = None,
        run_result: Optional[Dict[str, Any]] = None,
        requested_path: Optional[str] = None,
    ) -> str:
        path = analysis.get("path") or read_result.get("path") or requested_path or "target.py"
        symbols = analysis.get("symbols") or {}
        functions = [f for f in (symbols.get("functions") or []) if not f.get("is_method")]
        classes = symbols.get("classes") or []

        lines: List[str] = [f"# 测试建议：`{path}`", ""]

        if write_result:
            written = str(write_result.get("path") or "")
            directory = written.rsplit("/", 1)[0] if "/" in written else "."
            filename = written.rsplit("/", 1)[-1]
            lines += [
                "## 已生成的测试文件",
                "",
                f"- 路径：`{written}`（{write_result.get('bytes', 0)} 字节）",
                "- 框架：unittest（Python 标准库，无需额外依赖）",
                f"- 运行方式：`python -m unittest discover -s {directory} -p {filename}`",
                "",
            ]

        lines += [
            "## 待覆盖目标",
            "",
            "| 符号 | 位置 | 建议覆盖的用例 |",
            "| --- | --- | --- |",
        ]
        for item in functions:
            lines.append(f"| `{item['name']}()` | L{item['line']} | 正常输入、边界值、异常输入 |")
        for item in classes:
            lines.append(f"| `{item['name']}` | L{item['line']} | 构造与每个公开方法的正常 / 异常路径 |")
        if not functions and not classes:
            lines.append("| （未发现可测符号） | - | 建议先重构出可测试的函数 |")

        lines += [
            "",
            "## 生成的测试代码",
            "",
            "```python",
            self._build_test_scaffold(path, analysis).rstrip(),
            "```",
        ]

        if run_result:
            lines += ["", "## 实际运行结果", ""]
            if run_result.get("timeout"):
                lines.append(f"- **超时被终止**（超过 {run_result['timeout']} 秒）")
            elif run_result.get("error"):
                lines.append(f"- 未能执行：{run_result['error']}")
            else:
                total = run_result.get("tests_run")
                passed = run_result.get("passed_count")
                skipped = run_result.get("skipped") or 0
                failed = run_result.get("failure_count") or 0
                state = "全部通过" if run_result.get("passed") else f"{failed} 个用例失败"
                lines += [
                    f"- 退出码 {run_result.get('returncode')}，{state}",
                    f"- 用例总数 {total}：通过 {passed}，跳过 {skipped}（待补断言），失败 {failed}",
                    f"- 耗时 {run_result.get('duration_ms', 0)} ms",
                ]
                if run_result.get("failures"):
                    lines += ["", "失败明细：", *[f"- `{item}`" for item in run_result["failures"][:5]]]
            lines += [
                "",
                "原始输出（节选）：",
                "",
                "```text",
                str(run_result.get("output") or "").strip()[:1200],
                "```",
            ]

        lines += [
            "",
            "> 说明：模块导入检查与符号存在性由本次自动生成并真实执行；带 `skipTest` 的用例需要结合业务语义补断言。",
            "> 配置 API Key 后，同一模式会由大模型直接写出带断言的完整用例并自动修复失败。",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------ 生成与重构
    def _generate_answer(
        self,
        write_result: Dict[str, Any],
        run_result: Optional[Dict[str, Any]],
        user_text: str,
        path: Optional[str],
    ) -> str:
        target = write_result.get("path") or path or "<未写入>"
        lines = [
            f"# 代码生成：`{target}`",
            "",
            "## 需求理解",
            "",
            f"- 原始描述：{self._cell(re.sub(r'\s+', ' ', user_text.strip()))[:200]}",
            "- 输入 / 输出 / 边界条件：需要结合业务语义补全（离线骨架不含语义推断）",
            "",
            "## 已生成",
            "",
            f"- 文件：`{target}`（{write_result.get('bytes', 0)} 字节，{write_result.get('lines', 0)} 行）",
            "- 结构：模块 docstring + 函数骨架 + `__main__` 入口",
            "",
            "## 验证结果",
            "",
        ]
        if run_result:
            code = run_result.get("returncode")
            lines.append(
                f"- `python {target}` → 退出码 {code}"
                f"（{'通过' if code == 0 else '失败'}），耗时 {run_result.get('duration_ms', 0)} ms"
            )
            out = str(run_result.get("stdout") or "").strip()
            err = str(run_result.get("stderr") or "").strip()
            if out:
                lines += ["", "```text", out[:300], "```"]
            if err:
                lines += ["", "标准错误：", "```text", err[:300], "```"]
        else:
            lines.append("- 未执行验证。")

        lines += [
            "",
            "## 下一步",
            "",
            "- 离线规则引擎无法凭空推断业务语义，只能给出可运行的骨架；",
            "  在 `.env` 中配置 API Key 后，同一模式会写出完整实现（含类型标注、docstring 与边界处理）并自测通过。",
        ]
        return "\n".join(lines)

    def _refactor_answer(
        self,
        read_result: Dict[str, Any],
        analysis: Dict[str, Any],
        write_result: Optional[Dict[str, Any]],
        diff_result: Optional[Dict[str, Any]],
        path: Optional[str],
    ) -> str:
        source_path = analysis.get("path") or read_result.get("path") or path or "目标文件"
        findings = analysis.get("findings") or []
        metrics = analysis.get("metrics") or {}
        category_map = {
            "PY001": "安全类",
            "PY003": "安全类",
            "PY004": "安全类",
            "SYN001": "安全类",
            "PY002": "结构类",
            "PY005": "结构类",
            "PY006": "结构类",
            "PY008": "结构类",
            "PY010": "结构类",
            "PY013": "结构类",
        }
        groups: Dict[str, List[Dict[str, Any]]] = {"安全类": [], "结构类": [], "可读性类": []}
        for finding in findings:
            groups[category_map.get(finding.get("code", ""), "可读性类")].append(finding)

        lines = [
            f"# 重构建议：`{source_path}`",
            "",
            f"共发现 {len(findings)} 处坏味道（代码 {metrics.get('total_lines', 0)} 行、"
            f"{metrics.get('functions', 0)} 个函数、最长函数 {metrics.get('max_function_length', 0)} 行）。",
            "",
            "## 一、坏味道清单",
            "",
            "| 类别 | 位置 | 坏味道 | 危害 | 重构手法 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for group_name in ("安全类", "结构类", "可读性类"):
            for finding in groups[group_name]:
                lines.append(
                    f"| {group_name} | L{finding.get('line', 0)} | "
                    f"{self._cell(finding.get('title', ''))} | {self._cell(finding.get('message', ''))} | "
                    f"{self._cell(finding.get('suggestion', ''))} |"
                )
        if not findings:
            lines.append("| - | - | 未发现明显坏味道 | - | 可以进入功能开发 |")

        lines += ["", "## 二、本次已安全落地的重构", ""]
        if diff_result and diff_result.get("changed"):
            lines += [
                f"- 改动：`{diff_result.get('left')}` → `{diff_result.get('right')}`",
                f"- 规模：新增 {diff_result.get('added_lines', 0)} 行，删除 {diff_result.get('removed_lines', 0)} 行",
                "- 手法：`== None` / `!= None` 替换为 `is None` / `is not None`（语义等价、可机械验证）",
                "",
                "```diff",
                str(diff_result.get("diff") or "")[:4000],
                "```",
            ]
        else:
            lines.append("- 未发现可以安全机械替换的部分（例如文件中没有 `== None` 这类写法）。")
        if write_result:
            lines += [
                "",
                f"- 重构后的完整文件：`{write_result.get('path')}`（原文件保持不变，可直接对比运行）",
            ]

        lines += ["", "## 三、建议的后续处理顺序", ""]
        ordered = groups["安全类"] + groups["结构类"] + groups["可读性类"]
        for index, finding in enumerate(ordered[:6], 1):
            lines.append(
                f"{index}. **L{finding.get('line', 0)} {self._cell(finding.get('title', ''))}** —— "
                f"{self._cell(finding.get('suggestion', ''))}"
            )
        if not ordered:
            lines.append("1. 当前没有需要优先处理的重构项。")

        lines += [
            "",
            "> 机械替换只覆盖语义等价的部分；异常处理、安全边界、函数拆分等改动需要人工评审后实施，",
            "> 每完成一项建议跑一次 `run_tests` 做回归。",
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
