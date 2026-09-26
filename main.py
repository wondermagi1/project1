#!/usr/bin/env python3
"""代码助手 Agent 的命令行入口。

用法示例::

    python main.py "审查 examples/buggy_sample.py"
    python main.py --provider mock "解释 examples/clean_sample.py"
    python main.py            # 进入交互模式
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.cli import main  # noqa: E402  (需要先修正 sys.path)

if __name__ == "__main__":
    raise SystemExit(main())
