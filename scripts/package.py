#!/usr/bin/env python3
"""把项目打包成「学号姓名.zip」提交包。

用法::

    python scripts/package.py --sid 20250001 --name 张三
    python scripts/package.py --sid 20250001 --name 张三 --output ../
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
MAX_PACKAGE_BYTES = 200 * 1024 * 1024


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
        raise ValueError("缺少提交文件：" + ", ".join(missing))
    if target.exists() and not force:
        raise SystemExit(f"目标文件已存在：{target}\n如需覆盖请加 --force")
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files(root):
            archive.write(path, arcname=f"{package_name}/{path.relative_to(root).as_posix()}")
            count += 1
    if target.stat().st_size >= MAX_PACKAGE_BYTES:
        raise ValueError(f"提交包超过 200 MB，请精简文件后重新打包：{target}")
    return count


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="打包作业提交文件")
    parser.add_argument("--sid", required=True, help="学号，例如 20250001")
    parser.add_argument("--name", required=True, help="姓名，例如 张三")
    parser.add_argument("--output", default=None, help="输出目录，默认项目上一级目录")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的压缩包")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    try:
        package_name = validate_package_name(f"{args.sid.strip()}{args.name.strip()}")
    except ValueError as exc:
        parser.error(str(exc))
    output_dir = Path(args.output).expanduser().resolve() if args.output else root.parent
    target = output_dir / f"{package_name}.zip"

    try:
        count = build_package(root, target, package_name, force=args.force)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    size_kb = target.stat().st_size / 1024
    print(f"已生成提交包：{target}")
    print(f"包含 {count} 个文件，压缩后 {size_kb:.1f} KB（上限 200 MB）")
    print("提交前请确认：")
    print("- 代码已推送到 GitHub / Gitee 仓库，并把仓库地址写进 README")
    print("- 压缩包名称为「学号姓名」，按要求提交到 001Homework1 目录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
