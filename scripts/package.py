#!/usr/bin/env python3
"""把项目打包成「学号姓名.zip」提交包。

用法::

    python scripts/package.py --sid 20250001 --name 张三
    python scripts/package.py --sid 20250001 --name 张三 --output ../
"""

from __future__ import annotations

import argparse
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
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
}
EXCLUDE_FILES = {".env", ".DS_Store", "desktop.ini"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip", ".rar", ".7z", ".log"}


def iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in relative.parts):
            continue
        if path.name in EXCLUDE_FILES or path.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        yield path


def build_package(root: Path, target: Path, package_name: str, force: bool = False) -> int:
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
    parser = argparse.ArgumentParser(description="打包作业提交文件")
    parser.add_argument("--sid", required=True, help="学号，例如 20250001")
    parser.add_argument("--name", required=True, help="姓名，例如 张三")
    parser.add_argument("--output", default=None, help="输出目录，默认项目上一级目录")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的压缩包")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    package_name = f"{args.sid.strip()}{args.name.strip()}"
    output_dir = Path(args.output).expanduser().resolve() if args.output else root.parent
    target = output_dir / f"{package_name}.zip"

    count = build_package(root, target, package_name, force=args.force)
    size_kb = target.stat().st_size / 1024
    print(f"已生成提交包：{target}")
    print(f"包含 {count} 个文件，压缩后 {size_kb:.1f} KB（上限 200 MB）")
    print("提交前请确认：")
    print("- 代码已推送到 GitHub / Gitee 仓库，并把仓库地址写进 README")
    print("- 压缩包名称为「学号姓名」，按要求提交到 001Homework1 目录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
