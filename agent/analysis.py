"""轻量静态分析引擎。

它不依赖任何第三方库，用标准库 ``ast`` 对 Python 代码做结构化检查，
并对其余语言做通用文本检查，为 Agent 提供「可验证的事实」——
行号、严重度、修复建议都由这里产出，避免大模型编造问题。
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Set

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEVERITY_WEIGHT = {"high": 25, "medium": 10, "low": 4}
SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}

MAX_LINE_LENGTH = 100
LONG_FUNCTION_LINES = 30
VERY_LONG_FUNCTION_LINES = 60
MAX_ARGS = 5

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "shell",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".html": "html",
    ".css": "css",
    ".txt": "text",
}


@dataclass
class Finding:
    """一条静态分析结论。``line`` 为 1 起始的行号。"""

    code: str
    severity: str
    line: int
    title: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def guess_language(filename: str) -> str:
    lowered = (filename or "").lower()
    for suffix, language in LANGUAGE_BY_SUFFIX.items():
        if lowered.endswith(suffix):
            return language
    return "text"


def analyze_source(code: str, filename: str = "<snippet>") -> Dict[str, Any]:
    """分析源码，返回结构化报告（可直接 JSON 序列化）。"""

    lines = code.splitlines()
    language = guess_language(filename)
    metrics = _base_metrics(code, lines, language)
    symbols: Dict[str, Any] = {"classes": [], "functions": [], "imports": []}
    findings: List[Finding] = []
    parse_error: Optional[str] = None

    if language == "python":
        tree: Optional[ast.AST]
        try:
            tree = ast.parse(code, filename=filename)
        except SyntaxError as exc:
            parse_error = f"{exc.msg}（第 {exc.lineno} 行）"
            findings.append(
                Finding(
                    code="SYN001",
                    severity="high",
                    line=exc.lineno or 1,
                    title="语法错误",
                    message=f"代码无法被 Python 解析：{parse_error}",
                    suggestion="先修复语法错误，之后才能进行完整分析。",
                )
            )
            tree = None
        if tree is not None:
            symbols = _collect_symbols(tree)
            metrics.update(_python_metrics(tree, lines, symbols))
            findings.extend(_check_python(tree, lines, symbols))

    findings.extend(_check_generic(code, lines))
    findings = _dedupe_and_sort(findings)
    score, grade = _score(findings)

    return {
        "path": filename,
        "language": language,
        "parse_error": parse_error,
        "metrics": metrics,
        "symbols": symbols,
        "findings": [finding.to_dict() for finding in findings],
        "score": score,
        "grade": grade,
    }


# --------------------------------------------------------------------- 指标
def _base_metrics(code: str, lines: Sequence[str], language: str) -> Dict[str, int]:
    blank = sum(1 for line in lines if not line.strip())
    if language == "python":
        comment = sum(1 for line in lines if line.strip().startswith("#"))
    else:
        comment = sum(1 for line in lines if line.strip().startswith(("#", "//", "*", "/*")))
    return {
        "total_lines": len(lines),
        "code_lines": max(len(lines) - blank - comment, 0),
        "blank_lines": blank,
        "comment_lines": comment,
        "chars": len(code),
    }


def _python_metrics(tree: ast.AST, lines: Sequence[str], symbols: Dict[str, Any]) -> Dict[str, int]:
    functions = symbols.get("functions", [])
    lengths = [item.get("length", 0) for item in functions] or [0]
    return {
        "functions": len(functions),
        "classes": len(symbols.get("classes", [])),
        "imports": len(symbols.get("imports", [])),
        "max_function_length": max(lengths),
        "avg_function_length": round(sum(lengths) / len(lengths), 1),
    }


def _dotted_name(node: Optional[ast.AST]) -> str:
    """把 ``os.path.join`` 这类属性访问还原成字符串。"""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return ""


def _decorator_names(node: ast.AST) -> List[str]:
    names: List[str] = []
    for decorator in getattr(node, "decorator_list", []) or []:
        name = _dotted_name(decorator)
        if name:
            names.append(name)
    return names


def _collect_symbols(tree: ast.AST) -> Dict[str, Any]:
    class_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    method_ids: Set[int] = set()
    for class_node in class_nodes:
        for child in class_node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_ids.add(id(child))

    classes = [
        {
            "name": node.name,
            "line": node.lineno,
            "end_line": getattr(node, "end_lineno", node.lineno),
            "methods": [c.name for c in node.body if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))],
            "bases": [name for name in (_dotted_name(b) for b in node.bases) if name],
            "has_docstring": bool(ast.get_docstring(node)),
        }
        for node in sorted(class_nodes, key=lambda item: item.lineno)
    ]

    functions = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = [arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
        end_line = getattr(node, "end_lineno", node.lineno)
        functions.append(
            {
                "name": node.name,
                "line": node.lineno,
                "end_line": end_line,
                "length": end_line - node.lineno + 1,
                "args": args,
                "is_method": id(node) in method_ids,
                "is_public": not node.name.startswith("_"),
                "has_docstring": bool(ast.get_docstring(node)),
                "decorators": _decorator_names(node),
                "async": isinstance(node, ast.AsyncFunctionDef),
            }
        )
    functions.sort(key=lambda item: item["line"])

    imports: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    {
                        "name": alias.asname or alias.name.split(".")[0],
                        "module": alias.name,
                        "line": node.lineno,
                        "wildcard": False,
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports.append(
                    {
                        "name": alias.asname or alias.name,
                        "module": node.module or "",
                        "line": node.lineno,
                        "wildcard": alias.name == "*",
                    }
                )
    imports.sort(key=lambda item: item["line"])

    return {"classes": classes, "functions": functions, "imports": imports}


# --------------------------------------------------------------------- 检查
def _check_python(tree: ast.AST, lines: Sequence[str], symbols: Dict[str, Any]) -> List[Finding]:
    findings: List[Finding] = []
    all_nodes = list(ast.walk(tree))

    # 1) 异常处理
    for node in all_nodes:
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            findings.append(
                Finding(
                    "PY001",
                    "high",
                    node.lineno,
                    "使用了裸 except",
                    "会捕获所有异常（包括 KeyboardInterrupt、SystemExit），真实错误容易被掩盖。",
                    "改为捕获具体异常类型，例如 `except ValueError as exc:`；确实需要兜底时用 `except Exception`。",
                )
            )
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            findings.append(
                Finding(
                    "PY002",
                    "medium",
                    node.lineno,
                    "异常被静默吞掉",
                    "except 分支只写了 pass，出错时没有任何日志，线上问题难以定位。",
                    "至少记录日志或重新抛出：`logger.exception(\"...\")` 或 `raise`。",
                )
            )

    # 2) 危险调用
    for node in all_nodes:
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
            findings.append(
                Finding(
                    "PY003",
                    "high",
                    node.lineno,
                    f"使用了 {node.func.id}()",
                    "动态执行字符串代码，输入不可信时会直接造成任意代码执行。",
                    "改用 `ast.literal_eval` 解析字面量，或改为显式的映射表 / 分发函数。",
                )
            )
        if name in {"os.system", "os.popen"}:
            findings.append(
                Finding(
                    "PY004",
                    "high",
                    node.lineno,
                    "通过 shell 执行命令",
                    f"`{name}` 会把字符串交给 shell 解释，存在命令注入风险且无法拿到返回码。",
                    "改用 `subprocess.run([...], check=True, timeout=...)` 以列表形式传参。",
                )
            )
        elif name.startswith("subprocess."):
            has_shell = any(
                kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            )
            if has_shell:
                findings.append(
                    Finding(
                        "PY004",
                        "high",
                        node.lineno,
                        "subprocess 使用 shell=True",
                        "命令字符串由 shell 解释，参数拼接时会引入命令注入。",
                        "去掉 `shell=True`，把参数拆成列表传入；需要管道时用 `shlex` 或改为 Python 实现。",
                    )
                )
            elif not any(kw.arg == "timeout" for kw in node.keywords):
                findings.append(
                    Finding(
                        "PY005",
                        "low",
                        node.lineno,
                        "子进程调用未设置超时",
                        "外部命令挂起时，父进程会被无限期阻塞。",
                        "补充 `timeout=` 参数，并处理 `subprocess.TimeoutExpired`。",
                    )
                )

    # 3) 可变默认参数
    for node in all_nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
        for default in defaults:
            if _is_mutable_literal(default):
                findings.append(
                    Finding(
                        "PY006",
                        "medium",
                        getattr(default, "lineno", node.lineno),
                        "参数默认值使用了可变对象",
                        "默认值在函数定义时创建并被所有调用共享，多次调用会互相污染数据。",
                        "默认值改为 `None`，函数内部再 `if arg is None: arg = []`。",
                    )
                )
                break

    # 4) 与 None 的比较
    for node in all_nodes:
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left] + list(node.comparators)
        if any(isinstance(item, ast.Constant) and item.value is None for item in operands):
            findings.append(
                Finding(
                    "PY007",
                    "low",
                    node.lineno,
                    "使用 == / != 与 None 比较",
                    "None 是单例，用等号比较可能被自定义的 __eq__ 影响，也不符合 PEP 8。",
                    "改为 `is None` / `is not None`。",
                )
            )

    # 5) 函数长度与文档
    for item in symbols.get("functions", []):
        length = item.get("length", 0)
        if length > VERY_LONG_FUNCTION_LINES:
            findings.append(
                Finding(
                    "PY008",
                    "medium",
                    item["line"],
                    f"函数 {item['name']} 过长（{length} 行）",
                    "超长函数通常混合了多个职责，难以测试和维护。",
                    "按职责拆分为多个小函数，或提取参数对象。",
                )
            )
        elif length > LONG_FUNCTION_LINES:
            findings.append(
                Finding(
                    "PY008",
                    "low",
                    item["line"],
                    f"函数 {item['name']} 偏长（{length} 行）",
                    "函数超过 30 行后，阅读与单测成本明显上升。",
                    "考虑把内部逻辑按步骤抽取为独立函数。",
                )
            )
        if item.get("is_public") and length >= 4 and not item.get("has_docstring"):
            findings.append(
                Finding(
                    "PY009",
                    "low",
                    item["line"],
                    f"函数 {item['name']} 缺少文档字符串",
                    "公开函数没有说明参数、返回值与异常，接口意图不清晰。",
                    "补充 docstring，至少写清参数含义、返回值与可能抛出的异常。",
                )
            )
        args = item.get("args", [])
        extra = len(args) - 1 if item.get("is_method") else len(args)
        if extra > MAX_ARGS:
            findings.append(
                Finding(
                    "PY010",
                    "low",
                    item["line"],
                    f"函数 {item['name']} 参数过多（{extra} 个）",
                    "参数列表过长会降低可读性，调用时容易传错顺序。",
                    "把相关参数收敛为 dataclass / 配置对象。",
                )
            )

    for item in symbols.get("classes", []):
        if item.get("has_docstring"):
            continue
        findings.append(
            Finding(
                "PY009",
                "low",
                item["line"],
                f"类 {item['name']} 缺少文档字符串",
                "类的职责与使用方式没有文字说明。",
                "补充 docstring 说明该类负责什么、如何使用。",
            )
        )

    # 6) 未使用的导入
    used_names = {node.id for node in all_nodes if isinstance(node, ast.Name)}
    used_names |= {node.attr for node in all_nodes if isinstance(node, ast.Attribute)}
    for item in symbols.get("imports", []):
        name = item.get("name", "")
        if item.get("wildcard"):
            findings.append(
                Finding(
                    "PY011",
                    "medium",
                    item["line"],
                    "使用了通配符导入",
                    f"`from {item.get('module', '')} import *` 会污染命名空间，且无法静态判断符号来源。",
                    "改为显式导入需要的符号。",
                )
            )
            continue
        if name == "annotations":
            continue
        if name and name not in used_names:
            findings.append(
                Finding(
                    "PY012",
                    "low",
                    item["line"],
                    f"导入的 {name} 未被使用",
                    "无用的导入会增加阅读负担，也可能掩盖循环依赖。",
                    "删除该导入，或用 `__all__` 明确说明它是对外导出的接口。",
                )
            )

    # 7) open() 未使用 with
    with_targets = {
        id(item.context_expr)
        for node in all_nodes
        if isinstance(node, ast.With)
        for item in node.items
    }
    for node in all_nodes:
        if isinstance(node, ast.Call) and _dotted_name(node.func) == "open" and id(node) not in with_targets:
            findings.append(
                Finding(
                    "PY013",
                    "medium",
                    node.lineno,
                    "open() 未使用 with 语句",
                    "异常发生时文件句柄不会被及时释放，可能造成资源泄漏。",
                    "改为 `with open(path, encoding=\"utf-8\") as fh:`。",
                )
            )

    return findings


def _is_mutable_literal(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"list", "dict", "set"}:
        return True
    return False


def _check_generic(code: str, lines: Sequence[str]) -> List[Finding]:
    findings: List[Finding] = []

    long_lines = [(index, line) for index, line in enumerate(lines, 1) if len(line) > MAX_LINE_LENGTH]
    for index, line in long_lines[:3]:
        findings.append(
            Finding(
                "GEN001",
                "low",
                index,
                f"单行超过 {MAX_LINE_LENGTH} 字符（{len(line)}）",
                "过长的行在 code review、diff 与双栏编辑器中都难以阅读。",
                "按语义换行，或拆分为多个表达式。",
            )
        )

    todo_re = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)
    todo_hits = [(index, line) for index, line in enumerate(lines, 1) if todo_re.search(line)]
    for index, _ in todo_hits[:3]:
        findings.append(
            Finding(
                "GEN002",
                "low",
                index,
                "遗留的 TODO / FIXME",
                "未完成的标记如果长期留在代码里，会逐步失去上下文。",
                "补齐实现，或转成带负责人和截止时间的 issue。",
            )
        )

    tab_lines = [index for index, line in enumerate(lines, 1) if "\t" in line]
    if tab_lines:
        findings.append(
            Finding(
                "GEN003",
                "low",
                tab_lines[0],
                "使用了制表符缩进",
                f"共 {len(tab_lines)} 行包含 Tab，混用空格会导致缩进不一致。",
                "统一改为 4 个空格，并用编辑器配置自动转换。",
            )
        )

    if code and not code.endswith("\n"):
        findings.append(
            Finding(
                "GEN004",
                "low",
                max(len(lines), 1),
                "文件结尾缺少换行",
                "POSIX 文本文件约定以换行结尾，部分工具链会给出告警。",
                "在文件末尾补一个换行符。",
            )
        )

    return findings


def _dedupe_and_sort(findings: Sequence[Finding]) -> List[Finding]:
    seen = set()
    unique: List[Finding] = []
    for finding in findings:
        key = (finding.code, finding.line, finding.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    unique.sort(key=lambda item: (SEVERITY_ORDER.get(item.severity, 9), item.line, item.code))
    return unique


def _score(findings: Sequence[Finding]) -> tuple:
    penalty = sum(SEVERITY_WEIGHT.get(finding.severity, 5) for finding in findings)
    score = max(0, min(100, 100 - penalty))
    if score >= 90:
        grade = "优秀"
    elif score >= 75:
        grade = "良好"
    elif score >= 60:
        grade = "一般，建议尽快修复高优先级问题"
    else:
        grade = "较差，存在明显的正确性或安全风险"
    return score, grade


def summarize_findings(findings: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    summary = {"high": 0, "medium": 0, "low": 0, "total": len(findings)}
    for finding in findings:
        severity = finding.get("severity", "low")
        summary[severity] = summary.get(severity, 0) + 1
    return summary
