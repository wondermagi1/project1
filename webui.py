#!/usr/bin/env python3
"""代码助手 Agent 的 Web 界面入口。

用法::

    python webui.py                # 默认 http://127.0.0.1:8000/
    python webui.py --port 9000 --open
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.web import serve  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动代码助手 Agent 的 Web 界面")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument("--open", action="store_true", help="启动后自动用浏览器打开")
    parser.add_argument("--provider", choices=("auto", "openai", "mock"), default="auto")
    parser.add_argument("--read-only", action="store_true", help="禁止写文件、上传及执行代码")
    parser.add_argument("--no-exec", action="store_true", help="禁止执行代码和测试")
    parser.add_argument("--workspace", help="要打开的项目目录，默认当前目录")
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost"):
        print("提示：监听非本机地址会把「能执行代码」的 Agent 暴露到局域网，请谨慎。")
    serve(host=args.host, port=args.port, open_browser=args.open, provider=args.provider,
          read_only=args.read_only, allow_exec=not args.no_exec, workspace=args.workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
