"""工具层：Agent 能调用的全部能力。

约定：

* 每个工具接收一个 ``dict`` 参数，返回一个可 JSON 序列化的 ``dict``。
* 统一带 ``ok`` 字段，失败时带 ``error``，便于模型判断下一步动作。
* 所有路径都限制在 ``config.workspace`` 内，防止越权读写宿主文件。
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .analysis import analyze_source, summarize_findings
from .config import AgentConfig
from .project import ProjectTools

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".agent_sessions",
}

MAX_LIST_ENTRIES = 200
MAX_SEARCH_RESULTS = 200
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 6000


class ToolError(Exception):
    """工具执行失败（参数非法、路径越界、外部命令异常等）。"""


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Callable[[Dict[str, Any]], Dict[str, Any]]
    dangerous: bool = False

    def to_spec(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling 的工具描述。"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表：负责校验、调用与错误兜底。"""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._tools: Dict[str, Tool] = {}
        self.calls: List[Dict[str, Any]] = []

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> List[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"未知工具 `{name}`，可用工具：{', '.join(self.names())}")
        return tool

    def specs(self) -> List[Dict[str, Any]]:
        return [tool.to_spec() for tool in self._tools.values()]

    def describe(self) -> List[Dict[str, str]]:
        return [
            {"name": tool.name, "description": tool.description, "dangerous": tool.dangerous}
            for tool in self._tools.values()
        ]

    def execute(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行工具，永不抛异常——错误会转成结构化结果交给模型处理。"""

        started = time.perf_counter()
        args = arguments if isinstance(arguments, dict) else {}
        try:
            tool = self.get(name)
            if tool.name in ("run_python", "run_tests") and not self.config.allow_exec:
                raise ToolError("代码执行已被禁用（--no-exec）")
            if tool.dangerous and self.config.read_only:
                raise ToolError(f"当前处于只读模式，已拒绝执行 `{name}`")
            result = tool.func(args)
            if not isinstance(result, dict):
                result = {"ok": True, "result": result}
        except ToolError as exc:
            result = {"ok": False, "error": str(exc)}
        except FileNotFoundError as exc:
            result = {"ok": False, "error": f"文件不存在：{exc}"}
        except PermissionError as exc:
            result = {"ok": False, "error": f"权限不足：{exc}"}
        except subprocess.TimeoutExpired as exc:
            result = {"ok": False, "error": f"命令超时：{exc}"}
        except json.JSONDecodeError as exc:
            result = {"ok": False, "error": f"JSON 解析失败：{exc}"}
        except Exception as exc:  # noqa: BLE001 —— 兜底，保证 Agent 循环不被打断
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        result.setdefault("tool", name)
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        self.calls.append({"tool": name, "arguments": args, "ok": bool(result.get("ok"))})
        return result


# --------------------------------------------------------------------- 实现
class CodeTools:
    """内置工具的具体实现。"""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.workspace = Path(config.workspace)

    # ------------------------------------------------------------ 路径工具
    def _resolve(self, raw: Any, must_exist: bool = False) -> Path:
        text = str(raw or "").strip().strip("`'\"")
        if not text:
            raise ToolError("缺少 `path` 参数")
        path = Path(text)
        if not path.is_absolute():
            path = self.workspace / path
        try:
            path = path.resolve()
        except OSError as exc:
            raise ToolError(f"无法解析路径 {text}：{exc}") from exc
        if not self._within_workspace(path):
            raise ToolError(
                f"路径越界：{path} 不在工作区 {self.workspace} 内，出于安全考虑已拒绝访问"
            )
        if must_exist and not path.exists():
            raise ToolError(f"路径不存在：{self._display(path)}")
        return path

    def _within_workspace(self, path: Path) -> bool:
        try:
            path.relative_to(self.workspace)
            return True
        except ValueError:
            return False

    def _display(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> Dict[str, Any]:
        if len(text) <= limit:
            return {"text": text, "truncated": False}
        return {"text": text[:limit] + "\n...（输出已截断）", "truncated": True}

    # ------------------------------------------------------------ read_file
    def read_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args.get("path"), must_exist=True)
        if path.is_dir():
            raise ToolError(f"{self._display(path)} 是目录，请使用 list_dir")
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            raise ToolError(f"{self._display(path)} 看起来是二进制文件，无法按文本读取")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")

        lines = text.splitlines()
        total = len(lines)
        if total == 0:
            return {
                "ok": True,
                "path": self._display(path),
                "total_lines": 0,
                "start_line": 0,
                "end_line": 0,
                "truncated": False,
                "content": "",
                "note": "文件为空",
            }

        try:
            start = max(1, int(args.get("start_line") or 1))
            end = min(total, int(args.get("end_line") or total))
        except (TypeError, ValueError) as exc:
            raise ToolError(f"start_line / end_line 必须是整数：{exc}") from exc
        if start > total:
            raise ToolError(f"start_line={start} 超过文件总行数 {total}")
        if end < start:
            raise ToolError(f"end_line={end} 不能小于 start_line={start}")

        numbered: List[str] = []
        used = 0
        truncated = False
        last_line = start - 1
        for index in range(start, end + 1):
            row = f"{index:>5} | {lines[index - 1]}"
            if used + len(row) > self.config.max_file_chars:
                truncated = True
                break
            numbered.append(row)
            used += len(row) + 1
            last_line = index

        return {
            "ok": True,
            "path": self._display(path),
            "total_lines": total,
            "start_line": start,
            "end_line": last_line,
            "truncated": truncated or end < total,
            "content": "\n".join(numbered),
        }

    # ------------------------------------------------------------ list_dir
    def list_dir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args.get("path") or ".", must_exist=True)
        if not path.is_file():
            if not path.is_dir():
                raise ToolError(f"{self._display(path)} 既不是文件也不是目录")
        recursive = bool(args.get("recursive"))
        pattern = str(args.get("glob") or "*")

        entries: List[Dict[str, Any]] = []
        truncated = False
        if path.is_file():
            entries.append({"path": self._display(path), "type": "file", "size": path.stat().st_size})
        else:
            iterator = path.rglob(pattern) if recursive else path.glob(pattern)
            for item in sorted(iterator, key=lambda p: (p.is_dir(), str(p).lower())):
                if any(part in SKIP_DIRS for part in item.relative_to(path).parts):
                    continue
                if len(entries) >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
                entries.append(
                    {
                        "path": self._display(item),
                        "type": "dir" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else None,
                    }
                )

        return {
            "ok": True,
            "root": self._display(path),
            "recursive": recursive,
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
        }

    # ------------------------------------------------------- search_in_files
    def search_in_files(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("缺少 `query` 参数")
        root = self._resolve(args.get("path") or ".", must_exist=True)
        pattern = str(args.get("glob") or "*")
        use_regex = bool(args.get("regex"))
        try:
            limit = min(int(args.get("max_results") or 50), MAX_SEARCH_RESULTS)
        except (TypeError, ValueError):
            limit = 50

        matcher: Optional[re.Pattern] = None
        if use_regex:
            try:
                matcher = re.compile(query)
            except re.error as exc:
                raise ToolError(f"正则表达式无效：{exc}") from exc

        candidates = [root] if root.is_file() else sorted(root.rglob(pattern))
        matches: List[Dict[str, Any]] = []
        scanned = 0
        truncated = False

        for item in candidates:
            if len(matches) >= limit:
                truncated = True
                break
            if not item.is_file():
                continue
            if any(part in SKIP_DIRS for part in item.parts):
                continue
            try:
                if item.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                content = item.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for index, line in enumerate(content.splitlines(), 1):
                hit = matcher.search(line) if matcher else (query in line)
                if not hit:
                    continue
                matches.append(
                    {
                        "path": self._display(item),
                        "line": index,
                        "text": line.strip()[:200],
                    }
                )
                if len(matches) >= limit:
                    truncated = True
                    break

        return {
            "ok": True,
            "query": query,
            "path": self._display(root),
            "scanned_files": scanned,
            "count": len(matches),
            "truncated": truncated,
            "matches": matches,
        }

    # ------------------------------------------------------------ write_file
    def write_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve(args.get("path"))
        content = args.get("content")
        if content is None:
            raise ToolError("缺少 `content` 参数")
        content = str(content)
        if path.exists() and path.is_dir():
            raise ToolError(f"{self._display(path)} 是目录，不能写入")
        overwrite = bool(args.get("overwrite"))
        if path.exists() and not overwrite:
            raise ToolError(
                f"文件已存在：{self._display(path)}；如需覆盖，请显式传入 overwrite=true"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content, encoding="utf-8", newline="\n")
        return {
            "ok": True,
            "path": self._display(path),
            "bytes": len(content.encode("utf-8")),
            "overwritten": existed,
            "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
        }

    # ------------------------------------------------------------ run_python
    def run_python(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target: Optional[Path] = None
        code = args.get("code")
        if code is None and args.get("path"):
            target = self._resolve(args.get("path"), must_exist=True)
            if target.is_dir():
                raise ToolError(f"{self._display(target)} 是目录，无法运行")
            code = target.read_text(encoding="utf-8")
        if not code:
            raise ToolError("需要提供 `code` 或 `path` 参数")

        try:
            timeout = min(float(args.get("timeout") or self.config.exec_timeout), 60.0)
        except (TypeError, ValueError):
            timeout = self.config.exec_timeout

        command = [sys.executable, "-X", "utf8"]
        if target is not None:
            command.append(str(target))
            stdin_payload = None
        else:
            command += ["-", ]
            stdin_payload = str(code)

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                command,
                input=stdin_payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(self.workspace),
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": f"执行超时（超过 {timeout} 秒），进程已被终止",
                "timeout": timeout,
            }
        duration = round((time.perf_counter() - started) * 1000, 1)
        stdout = self._truncate(proc.stdout or "")
        stderr = self._truncate(proc.stderr or "")
        return {
            "ok": proc.returncode == 0,
            "path": self._display(target) if target else "<标准输入>",
            "returncode": proc.returncode,
            "duration_ms": duration,
            "stdout": stdout["text"],
            "stderr": stderr["text"],
            "output_truncated": stdout["truncated"] or stderr["truncated"],
        }

    # ---------------------------------------------------------- analyze_code
    def analyze_code(self, args: Dict[str, Any]) -> Dict[str, Any]:
        code = args.get("code")
        display = args.get("filename") or "<snippet>"
        if code is None and args.get("path"):
            path = self._resolve(args.get("path"), must_exist=True)
            if path.is_dir():
                raise ToolError(f"{self._display(path)} 是目录，请指定具体文件")
            code = path.read_text(encoding="utf-8", errors="replace")
            display = self._display(path)
        if code is None:
            raise ToolError("需要提供 `path` 或 `code` 参数")

        report = analyze_source(str(code), filename=str(display))
        report["ok"] = "parse_error" not in report or report["parse_error"] is None
        report["severity_summary"] = summarize_findings(report.get("findings", []))
        return report

    # ------------------------------------------------------------ run_tests
    def run_tests(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """用 unittest 真正跑一遍测试，并返回结构化结果。"""

        target = self._resolve(args.get("path") or ".", must_exist=True)
        pattern = str(args.get("pattern") or "").strip() or None
        if target.is_file():
            start_dir = target.parent
            pattern = pattern or target.name
        else:
            start_dir = target
            pattern = pattern or "test*.py"

        try:
            timeout = min(max(float(args.get("timeout") or 60.0), 5.0), 300.0)
        except (TypeError, ValueError):
            timeout = 60.0

        command = [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(start_dir),
            "-t",
            str(start_dir),
            "-p",
            pattern,
            "-v",
        ]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        started = time.perf_counter()
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(self.workspace),
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"测试执行超时（超过 {timeout} 秒）", "timeout": timeout}

        duration = round((time.perf_counter() - started) * 1000, 1)
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        ran_match = re.search(r"Ran (\d+) tests? in", output)
        failures = re.findall(r"^(?:FAIL|ERROR): (.+)$", output, re.M)
        skipped_match = re.search(r"skipped=(\d+)", output)
        total = int(ran_match.group(1)) if ran_match else None
        skipped = int(skipped_match.group(1)) if skipped_match else 0
        passed = (total - len(failures) - skipped) if total is not None else None
        truncated = self._truncate(output, 6000)

        return {
            # returncode 1 表示「有用例失败」，但工具本身执行成功，Agent 应据此修复而不是当成工具故障
            "ok": proc.returncode in (0, 1),
            "passed": proc.returncode == 0,
            "returncode": proc.returncode,
            "target": self._display(start_dir),
            "pattern": pattern,
            "tests_run": total,
            "passed_count": passed,
            "skipped": skipped,
            "failure_count": len(failures),
            "failures": [item.strip() for item in failures[:20]],
            "duration_ms": duration,
            "output": truncated["text"],
            "output_truncated": truncated["truncated"],
        }

    # ------------------------------------------------------------ diff_files
    def diff_files(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """生成两个版本之间的 unified diff（用于展示重构/注释建议）。"""

        left_raw = args.get("left") or args.get("path")
        if not left_raw:
            raise ToolError("需要提供 `left`（原始文件路径）")
        left_path = self._resolve(left_raw, must_exist=True)
        if left_path.is_dir():
            raise ToolError(f"{self._display(left_path)} 是目录，请指定具体文件")
        before = left_path.read_text(encoding="utf-8", errors="replace").splitlines()

        right_raw = args.get("right")
        content = args.get("content")
        if right_raw:
            right_path = self._resolve(right_raw, must_exist=True)
            after = right_path.read_text(encoding="utf-8", errors="replace").splitlines()
            right_label = self._display(right_path)
        elif content is not None:
            after = str(content).splitlines()
            right_label = "<建议版本>"
        else:
            raise ToolError("需要提供 `right`（对比文件）或 `content`（建议内容）")

        try:
            context = max(0, min(int(args.get("context") or 3), 20))
        except (TypeError, ValueError):
            context = 3

        diff_lines = list(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{self._display(left_path)}",
                tofile=f"b/{right_label}",
                lineterm="",
                n=context,
            )
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        text = "\n".join(diff_lines) if diff_lines else "（两处内容完全一致，没有差异）"
        truncated = self._truncate(text, 8000)
        return {
            "ok": True,
            "left": self._display(left_path),
            "right": right_label,
            "changed": bool(diff_lines),
            "added_lines": added,
            "removed_lines": removed,
            "diff": truncated["text"],
            "truncated": truncated["truncated"],
        }


# --------------------------------------------------------------------- 装配
def build_default_registry(config: AgentConfig) -> ToolRegistry:
    """注册全部内置工具。"""

    tools = CodeTools(config)
    registry = ToolRegistry(config)
    project = ProjectTools(config)
    registry.register(Tool("project_scan", "跨文件扫描项目质量，可只分析 Git 改动文件，输出问题、行号与严重度汇总。不会执行代码。", {"type": "object", "properties": {"max_files": {"type": "integer"}, "changed_only": {"type": "boolean"}}}, project.scan))
    registry.register(Tool("git_diff", "只读查看当前 Git 仓库暂存与未暂存改动，用于提交前审查。", {"type": "object", "properties": {"include_diff": {"type": "boolean"}}}, project.git_diff))

    registry.register(
        Tool(
            name="read_file",
            description=(
                "读取工作区内的文本文件，返回带行号的内容。"
                "可选 start_line / end_line 只读取部分行，避免一次读入过大文件。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区的文件路径"},
                    "start_line": {"type": "integer", "description": "起始行号，从 1 开始"},
                    "end_line": {"type": "integer", "description": "结束行号，包含该行"},
                },
                "required": ["path"],
            },
            func=tools.read_file,
        )
    )

    registry.register(
        Tool(
            name="list_dir",
            description="列出目录内容，用于在不确定文件位置时先探查项目结构。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认为工作区根目录"},
                    "recursive": {"type": "boolean", "description": "是否递归列出子目录"},
                    "glob": {"type": "string", "description": "文件名匹配模式，例如 *.py"},
                },
            },
            func=tools.list_dir,
        )
    )

    registry.register(
        Tool(
            name="search_in_files",
            description="在工作区内按关键字或正则搜索代码，返回命中的文件、行号与上下文行。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要搜索的关键字或正则表达式"},
                    "path": {"type": "string", "description": "搜索根目录，默认为工作区根目录"},
                    "glob": {"type": "string", "description": "限定文件模式，例如 *.py"},
                    "regex": {"type": "boolean", "description": "是否把 query 当作正则表达式"},
                    "max_results": {"type": "integer", "description": "最多返回多少条命中"},
                },
                "required": ["query"],
            },
            func=tools.search_in_files,
        )
    )

    registry.register(
        Tool(
            name="analyze_code",
            description=(
                "对代码做静态分析，返回代码规模、类/函数结构、问题清单（含行号、严重度、"
                "修复建议）与质量评分。这是代码审查的主要事实来源。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要分析的文件路径"},
                    "code": {"type": "string", "description": "直接分析的代码字符串"},
                    "filename": {"type": "string", "description": "传入 code 时用于判断语言的文件名"},
                },
            },
            func=tools.analyze_code,
        )
    )

    registry.register(
        Tool(
            name="run_python",
            description=(
                "在工作区中执行 Python 代码（传入 path 或 code），返回退出码、标准输出与错误输出，"
                "带超时保护。用于验证代码行为或复现问题。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要执行的 Python 文件路径"},
                    "code": {"type": "string", "description": "要执行的代码片段"},
                    "timeout": {"type": "number", "description": "超时秒数，默认 15 秒"},
                },
            },
            func=tools.run_python,
            dangerous=True,
        )
    )

    registry.register(
        Tool(
            name="run_tests",
            description=(
                "用 unittest 实际运行测试并返回结构化结果（用例数、通过数、失败项、原始输出）。"
                "可以传入测试文件或目录；生成测试后应当调用它来验证。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "测试文件或目录，默认为工作区根目录"},
                    "pattern": {"type": "string", "description": "文件名匹配模式，例如 test_*.py"},
                    "timeout": {"type": "number", "description": "超时秒数，默认 60 秒"},
                },
            },
            func=tools.run_tests,
            dangerous=True,
        )
    )

    registry.register(
        Tool(
            name="diff_files",
            description=(
                "生成 unified diff：比较两个文件，或比较「文件」与「建议内容」。"
                "用于在重构、补注释时展示具体改动，避免直接覆盖原文件。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "left": {"type": "string", "description": "原始文件路径"},
                    "right": {"type": "string", "description": "用于对比的另一个文件路径"},
                    "content": {"type": "string", "description": "建议的新内容（与 left 对比）"},
                    "context": {"type": "integer", "description": "上下文行数，默认 3"},
                },
                "required": ["left"],
            },
            func=tools.diff_files,
        )
    )

    registry.register(
        Tool(
            name="write_file",
            description=(
                "把内容写入工作区内的文件（例如生成测试文件）。"
                "默认拒绝覆盖已存在的文件，必须显式传入 overwrite=true。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                    "overwrite": {"type": "boolean", "description": "文件已存在时是否允许覆盖"},
                },
                "required": ["path", "content"],
            },
            func=tools.write_file,
            dangerous=True,
        )
    )

    return registry
