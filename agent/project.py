"""面向项目的只读工作区索引、批量分析和 Git 改动证据。"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict

from .analysis import analyze_source
from .config import AgentConfig

SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".cjs", ".mjs", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".html", ".css", ".sql", ".sh"}
TEXT_SUFFIXES = SOURCE_SUFFIXES | {".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".cjs", ".mjs"}
EXCLUDED = {".git", ".hg", ".svn", ".agent_sessions", ".test_workspaces", ".venv", "venv", "env", "node_modules", "dist", "build", "generated", "uploads", "__pycache__", ".pytest_cache", ".mypy_cache", ".idea", ".vscode"}
MAX_FILES = 500
MAX_FILE_BYTES = 300_000


def visible_path(path: Path) -> bool:
    return not any(part.startswith(".") or part in EXCLUDED for part in path.parts) and path.suffix.lower() in TEXT_SUFFIXES


class ProjectTools:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.root = config.workspace.resolve()

    def resolve_file(self, name: str) -> Path:
        relative = Path(name)
        target = (self.root / relative).resolve()
        if relative.is_absolute() or not visible_path(relative) or not target.is_relative_to(self.root):
            raise ValueError("只允许访问工作区中的项目文本文件")
        return target

    def inventory(self, args: Dict[str, Any] | None = None) -> Dict[str, Any]:
        files = []
        truncated = False
        for current, directories, names in os.walk(self.root, followlinks=False):
            allowed = []
            for name in sorted(directories):
                directory = Path(current) / name
                if name.startswith(".") or name in EXCLUDED or directory.is_symlink():
                    continue
                if hasattr(directory, "is_junction") and directory.is_junction():
                    continue
                if directory.resolve().is_relative_to(self.root):
                    allowed.append(name)
            directories[:] = allowed
            for name in sorted(names):
                target = Path(current) / name
                relative = target.relative_to(self.root)
                if not visible_path(relative) or target.is_symlink() or not target.resolve().is_relative_to(self.root):
                    continue
                try:
                    size = target.stat().st_size
                except OSError:
                    continue
                files.append({"path": relative.as_posix(), "bytes": size, "language": target.suffix.lstrip("."), "source": target.suffix.lower() in SOURCE_SUFFIXES})
                if len(files) >= MAX_FILES:
                    truncated = True
                    break
            if truncated:
                break
        languages = Counter(row["language"] for row in files if row["source"])
        names = {row["path"] for row in files}
        manifests = [name for name in ("pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod", "pom.xml") if (self.root / name).is_file()]
        return {"ok": True, "name": self.root.name, "files": files, "file_count": len(files), "source_count": sum(row["source"] for row in files), "languages": dict(languages), "manifests": manifests, "has_readme": any(name.lower() == "readme.md" for name in names), "has_tests": any(name.startswith("tests/") or Path(name).name.startswith("test_") for name in names), "truncated": truncated}

    def scan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        inventory = self.inventory()
        limit = max(1, min(int(args.get("max_files", 100)), 200))
        changed_only = bool(args.get("changed_only", False))
        changed = None
        if changed_only:
            git = self.git_diff({"include_diff": False})
            if not git["ok"]:
                return git
            changed = {item["path"] for item in git["files"]}
        candidates = [row for row in inventory["files"] if row["source"] and (changed is None or row["path"] in changed)]
        files, findings, skipped = [], [], []
        for row in candidates[:limit]:
            name = row["path"]
            try:
                path = self.resolve_file(name)
                with path.open("rb") as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if row["bytes"] > MAX_FILE_BYTES or len(raw) > MAX_FILE_BYTES:
                    skipped.append({"path": name, "reason": "文件超过 300 KB"})
                    continue
                if b"\x00" in raw:
                    skipped.append({"path": name, "reason": "包含二进制内容"})
                    continue
                result = analyze_source(raw.decode("utf-8-sig"), name)
            except (OSError, UnicodeError, ValueError, RecursionError) as exc:
                skipped.append({"path": name, "reason": str(exc)[:160]})
                continue
            items = result["findings"]
            files.append({"path": name, "score": result["score"], "language": result["language"], "lines": result["metrics"].get("total_lines", 0), "findings_count": len(items)})
            findings.extend({"path": name, **item} for item in items)
        ranks = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda row: (ranks.get(row["severity"], 3), row["path"], row["line"]))
        counts = Counter(item["severity"] for item in findings)
        return {"ok": True, "scope": "changed" if changed_only else "project", "files_scanned": len(files), "candidate_count": len(candidates), "files": sorted(files, key=lambda row: (row["score"], row["path"])), "findings": findings[:500], "findings_total": len(findings), "severity_summary": {"high": counts["high"], "medium": counts["medium"], "low": counts["low"], "total": len(findings)}, "average_score": round(sum(row["score"] for row in files) / len(files), 1) if files else None, "lines": sum(row["lines"] for row in files), "skipped": skipped, "truncated": inventory["truncated"] or len(candidates) > limit or len(findings) > 500, "duration_ms": round((time.perf_counter() - started) * 1000), "note": "Python 使用 AST 规则，其他语言使用通用文本规则。评分不代表正确性，未执行任何项目代码。"}

    def git_diff(self, args: Dict[str, Any]) -> Dict[str, Any]:
        executable = shutil.which("git")
        if not executable:
            return {"ok": False, "error": "未找到 Git，请先安装 Git 并加入 PATH"}
        def git(*arguments: str) -> subprocess.CompletedProcess:
            return subprocess.run([executable, "-c", "core.quotePath=false", "-c", "core.fsmonitor=false", "--no-pager", *arguments], cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        try:
            root = git("rev-parse", "--show-toplevel")
            if root.returncode or Path(root.stdout.strip()).resolve() != self.root:
                return {"ok": False, "error": "当前工作区不是 Git 仓库根目录，请使用 --workspace 指定仓库根目录"}
            status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
            if status.returncode:
                return {"ok": False, "error": "无法读取 Git 状态"}
            files = []
            for entry in status.stdout.split("\0"):
                if len(entry) < 4:
                    continue
                name = entry[3:]
                if not visible_path(Path(name)):
                    continue
                files.append({"path": name, "status": entry[:2], "untracked": entry[:2] == "??"})
            branch = git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "detached HEAD"
            selected = files[:100]
            sections = []
            if args.get("include_diff", True) and selected:
                paths = [":(literal)" + item["path"] for item in selected if not item["untracked"]]
                if paths:
                    for label, options in (("未暂存修改", []), ("已暂存修改", ["--cached"])):
                        result = git("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=3", *options, "--", *paths)
                        if result.returncode:
                            return {"ok": False, "error": "无法读取 Git 差异"}
                        if result.stdout:
                            sections.append(f"### {label}\n{result.stdout}")
            diff = "\n".join(sections)
            return {"ok": True, "branch": branch, "files": selected, "changed_count": len(files), "diff": diff[:40_000], "truncated": len(files) > 100 or len(diff) > 40_000, "note": "仅展示项目文本文件；新文件没有 Git diff，可打开预览或扫描当前内容。未执行提交、暂存或修改操作。"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": f"Git 读取失败：{exc}"}


def scan_markdown(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return "项目扫描失败：" + result.get("error", "未知错误")
    summary = result["severity_summary"]
    lines = ["# 项目质量扫描", "", f"扫描 {result['files_scanned']} 个文件，共 {result['lines']} 行。", f"发现 {summary['total']} 项规则问题：高 {summary['high']} / 中 {summary['medium']} / 低 {summary['low']}。", "", "| 严重度 | 文件 | 行号 | 问题 |", "| --- | --- | --- | --- |"]
    for row in result["findings"][:40]:
        safe_path = row["path"].replace("|", "\\|")
        lines.append(f"| {row['severity']} | {safe_path} | {row['line']} | {row['title']} |")
    if result["findings_total"] > 40:
        lines.extend(["", "此摘要只展示前 40 项，请在项目概览中筛选或导出完整扫描结果。"])
    lines.extend(["", result["note"], f"跳过 {len(result['skipped'])} 个文件。" , "结果已达到数量上限，请缩小工作区或按改动扫描。" if result["truncated"] else ""])
    return "\n".join(lines)
