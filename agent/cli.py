"""命令行交互界面。

支持两种用法：

* 一次性任务：``python main.py "审查 examples/buggy_sample.py"``
* 交互式对话：``python main.py``，支持 ``/help`` ``/tools`` ``/history`` 等命令
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .agent import AgentStep, CodeAgent
from .config import AgentConfig
from .llm import create_client
from .memory import ConversationMemory
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .tools import build_default_registry

BANNER = """代码助手 Agent v{version}
能力：代码审查 / 代码解释 / 测试生成，支持多轮记忆与工具调用
输入 /help 查看命令，输入 /exit 退出"""

HELP_TEXT = """可用命令（仅交互模式）：
  /help              显示本帮助
  /tools             列出可用工具
  /history           查看本次会话的对话记录
  /clear             清空当前会话上下文
  /session <名称>    切换 / 新建会话
  /save              手动保存会话
  /exit              退出

使用示例：
  审查 examples/buggy_sample.py
  解释 examples/clean_sample.py
  为 examples/buggy_sample.py 生成单元测试
  列出目录
  搜索 TODO
  运行 examples/buggy_sample.py
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-agent",
        description="代码助手 Agent：代码审查 / 代码解释 / 测试生成",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            '  python main.py "审查 examples/buggy_sample.py"\n'
            '  python main.py --provider mock "解释 examples/clean_sample.py"\n'
            "  python main.py --list-sessions\n"
        ),
    )
    parser.add_argument("prompt", nargs="*", help="一次性任务描述；省略则进入交互模式")
    parser.add_argument("-f", "--file", help="把指定文件追加到任务描述中")
    parser.add_argument(
        "--provider",
        choices=["auto", "openai", "mock"],
        default=None,
        help="LLM 提供方；auto 表示有 API Key 用在线模型，否则用离线规则引擎",
    )
    parser.add_argument("--model", default=None, help="模型名，例如 gpt-4o-mini")
    parser.add_argument("--base-url", default=None, help="OpenAI 兼容接口地址")
    parser.add_argument("--api-key", default=None, help="API Key（优先级高于环境变量）")
    parser.add_argument("--workspace", default=None, help="工作区目录，默认当前目录")
    parser.add_argument("--session", default="default", help="会话名称，默认 default")
    parser.add_argument("--new-session", action="store_true", help="忽略历史，开启新会话")
    parser.add_argument("--list-sessions", action="store_true", help="列出已保存的会话后退出")
    parser.add_argument("--read-only", action="store_true", help="只读模式：禁用写文件与执行代码")
    parser.add_argument("--no-exec", action="store_true", help="禁止执行 Python 代码（同只读执行）")
    parser.add_argument("--max-iterations", type=int, default=None, help="单轮任务的最大循环次数")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出执行轨迹与结果")
    parser.add_argument("-q", "--quiet", action="store_true", help="不显示执行轨迹")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示模型中间输出")
    parser.add_argument("--show-tools", action="store_true", help="列出可用工具后退出")
    parser.add_argument("--version", action="version", version=f"code-agent {__version__}")
    return parser


def _make_printer(quiet: bool, verbose: bool):
    def printer(step: AgentStep) -> None:
        if quiet:
            return
        if step.kind == "tool":
            status = "ok  " if step.ok else "fail"
            print(f"  [{status}] {step.title.replace('调用工具 ', '')}: {step.detail}")
        elif step.kind == "error":
            print(f"  [error] {step.title}: {step.detail}")
        elif step.kind == "llm" and verbose:
            preview = step.detail.replace("\n", " ")[:160]
            print(f"  [model] {preview}")

    return printer


def _build_config(args: argparse.Namespace) -> AgentConfig:
    workspace = Path(args.workspace).expanduser() if args.workspace else Path.cwd()
    read_only = bool(args.read_only)
    allow_exec = not (args.no_exec or args.read_only)
    return AgentConfig.from_env(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        workspace=workspace,
        max_iterations=args.max_iterations,
        read_only=read_only,
        allow_exec=allow_exec,
        verbose=args.verbose,
    )


def _build_agent(config: AgentConfig, args: argparse.Namespace) -> CodeAgent:
    memory = ConversationMemory.load(
        session_id=args.session,
        session_dir=config.session_dir,
        system_prompt=SYSTEM_PROMPT,
        autosave=True,
    )
    if args.new_session:
        memory.clear()
    return CodeAgent(
        config=config,
        client=create_client(config),
        registry=build_default_registry(config),
        memory=memory,
        on_event=_make_printer(args.quiet, args.verbose),
    )


def _print_answer(answer: str) -> None:
    print()
    print(answer)
    print()


def _handle_command(command: str, agent: CodeAgent, args: argparse.Namespace) -> bool:
    """处理交互式命令，返回 False 表示应退出。"""

    parts = command.split()
    name = parts[0].lower()
    if name in ("/exit", "/quit", "/q"):
        return False
    if name in ("/help", "/?"):
        print(HELP_TEXT)
    elif name == "/tools":
        print("可用工具：")
        for tool in agent.registry.describe():
            flag = "（危险操作，只读模式下禁用）" if tool["dangerous"] else ""
            print(f"  - {tool['name']}: {tool['description']}{flag}")
        print()
    elif name == "/history":
        rows = agent.memory.transcript()
        if not rows:
            print("（当前会话还没有对话记录）")
        for row in rows:
            print(f"[{row['role']}] {row['content']}")
        print()
    elif name == "/clear":
        agent.memory.clear()
        print("已清空当前会话上下文。\n")
    elif name == "/save":
        target = agent.memory.save()
        print(f"已保存会话：{target}\n")
    elif name == "/session":
        if len(parts) < 2:
            print("用法：/session <名称>\n")
            return True
        agent.memory.save()
        agent.memory = ConversationMemory.load(
            session_id=parts[1],
            session_dir=agent.config.session_dir,
            system_prompt=SYSTEM_PROMPT,
            autosave=True,
        )
        print(f"已切换到会话 `{agent.memory.session_id}`（{agent.memory.turns()} 轮历史）。\n")
    else:
        print(f"未知命令：{command}，输入 /help 查看可用命令。\n")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = _build_config(args)
    except (OSError, ValueError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    if args.list_sessions:
        sessions = ConversationMemory.list_sessions(config.session_dir)
        if not sessions:
            print(f"没有找到会话文件（目录：{config.session_dir}）")
            return 0
        for item in sessions:
            print(
                f"- {item['session_id']}: {item['message_count']} 条消息，最后更新 {item['updated_at']}"
            )
        return 0

    try:
        agent = _build_agent(config, args)
    except Exception as exc:  # noqa: BLE001 —— 启动失败要给出可读提示
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 2

    if args.show_tools:
        print(f"工作区：{config.workspace}")
        print(f"provider：{config.active_provider}")
        print("可用工具：")
        for tool in agent.registry.describe():
            print(f"  - {tool['name']}: {tool['description']}")
        return 0

    if not args.quiet:
        print(f"provider={config.active_provider}  model={config.model}  workspace={config.workspace}")
        if config.active_provider == "mock":
            print("提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。")

    # ---------------------------------------------------------- 一次性任务
    if args.prompt:
        task = build_user_prompt(" ".join(args.prompt).strip(), args.file)
        result = agent.run(task)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_answer(result.answer)
        return 1 if result.stopped == "error" else 0

    # ---------------------------------------------------------- 交互模式
    if not args.quiet:
        print(BANNER.format(version=__version__))
    while True:
        try:
            line = input("code-agent> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        text = line.strip()
        if not text:
            continue
        if text.startswith("/"):
            if not _handle_command(text, agent, args):
                break
            continue
        result = agent.run(text)
        _print_answer(result.answer)

    saved = agent.memory.save()
    if saved and not args.quiet:
        print(f"会话已保存：{saved}")
    return 0
