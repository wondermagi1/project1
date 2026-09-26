"""库存工具：只包含无副作用的纯函数与带超时的外部调用。"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

MAX_ITEMS = 100


@dataclass
class Item:
    """一条库存记录。"""

    name: str
    price: float
    quantity: int = 0

    def total(self) -> float:
        """返回该条目的库存金额。"""

        return round(self.price * self.quantity, 2)


def load_inventory(path: Path) -> List[Item]:
    """从 JSON 文件读取库存列表；文件不存在时返回空列表。"""

    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Item(**row) for row in payload]


def summarize(items: Iterable[Item], limit: int = MAX_ITEMS) -> Dict[str, float]:
    """统计库存条目数与总金额。"""

    selected = list(items)[:limit]
    return {
        "count": len(selected),
        "value": round(sum(item.total() for item in selected), 2),
    }


def run_report(mode: str, timeout: float = 10.0) -> str:
    """调用外部报表脚本，并处理超时与进程启动失败。"""

    try:
        proc = subprocess.run(
            ["python", "report.py", mode],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "报表生成超时"
    except OSError as exc:
        return f"报表生成失败：{exc}"
    return proc.stdout
