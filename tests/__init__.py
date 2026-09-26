"""单元测试包。

测试用的临时工作区统一创建在仓库内的 ``.test_workspaces/`` 下：
这样既不会污染系统临时目录，也方便在沙箱 / CI 环境中清理。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".test_workspaces"


def make_workspace(prefix: str = "case-") -> Path:
    """创建一个独立的临时工作区目录。"""

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(50):
        # 这里刻意用默认权限的 mkdir：某些受限沙箱下 0o700 的目录不可再写入。
        path = SCRATCH_ROOT / f"{prefix}{uuid.uuid4().hex[:8]}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path
    raise RuntimeError("无法创建临时工作区")


def remove_workspace(path: Path) -> None:
    """尽力清理临时工作区，失败时忽略（不掩盖测试本身的结论）。"""

    shutil.rmtree(path, ignore_errors=True)
