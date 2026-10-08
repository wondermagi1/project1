#!/usr/bin/env python3
"""将项目源码与文档打包为 ZIP 归档。

用法::

    python scripts/package.py
    python scripts/package.py --package-name code-assistant-agent-1.0.0 --output dist
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path
from typing import Iterator, List, Optional

EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".agent_sessions",
    ".test_workspaces",
    "uploads",
    "generated",
    ".venv",
    "venv",
    ".idea",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
}
EXCLUDE_FILES = {".env", ".DS_Store", "desktop.ini"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip", ".rar", ".7z", ".log"}


def validate_package_name(name: str) -> str:
    if not name or name in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
        raise ValueError("包名称不能为空或包含路径、文件名非法字符")
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
    if name.split(".")[0].upper() in reserved:
        raise ValueError("包名称不能使用系统保留名称")
    return name


def iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in relative.parts):
            continue
        if path.name in EXCLUDE_FILES or path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        yield path


def build_package(root: Path, target: Path, package_name: str, force: bool = False) -> int:
    validate_package_name(package_name)
    missing = [name for name in ("README.md", "Design.md", "main.py", "webui.py") if not (root / name).is_file()]
    if missing:
        raise ValueError("缺少项目文件：" + ", ".join(missing))
    if target.exists() and not force:
        raise SystemExit(f"目标文件已存在：{target}\n如需覆盖请加 --force")
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files(root):
            archive.write(path, arcname=f"{package_name}/{path.relative_to(root).as_posix()}")
            count += 1
    return count


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="打包项目源码与文档")
    parser.add_argument("--package-name", default="code-assistant-agent", help="归档名称，默认 code-assistant-agent")
    parser.add_argument("--output", default=None, help="输出目录，默认项目 dist 目录")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的压缩包")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    try:
        package_name = validate_package_name(args.package_name.strip())
    except ValueError as exc:
        parser.error(str(exc))
    output_dir = Path(args.output).expanduser().resolve() if args.output else root / "dist"
    target = output_dir / f"{package_name}.zip"

    try:
        count = build_package(root, target, package_name, force=args.force)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    size_kb = target.stat().st_size / 1024
    print(f"已生成源码归档：{target}")
    print(f"包含 {count} 个文件，压缩后 {size_kb:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
