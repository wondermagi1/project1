"""命令行交互界面。

支持两种用法：

* 一次性任务：``python main.py "审查 examples/buggy_sample.py"``
* 交互式对话：``python main.py``，支持 ``/help`` ``/tools`` ``/history`` 等命令
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import __version__
from .agent import AgentStep, CodeAgent
from .config import AgentConfig
from .llm import LLMError, create_client
from .memory import ConversationMemory
from .modes import AUTO_MODE, build_system_prompt, list_modes, resolve_mode
from .prompts import build_user_prompt
from .reporting import export_report
from .tools import build_default_registry

BANNER = """代码助手 Agent v{version}
任务模式：代码审查 / 代码解释 / 代码生成 / 测试生成 / 重构建议 / 通用问答
支持多轮记忆、工具调用与真实大模型（未配置 API Key 时自动使用离线规则引擎）
输入 /help 查看命令，输入 /exit 退出"""

HELP_TEXT = """可用命令（仅交互模式）：
  /help              显示本帮助
  /mode [名称]       查看或切换任务模式
                     review 审查 / explain 解释 / generate 生成 / test 测试 / refactor 重构 / ask 问答 / auto 自动
  /tools             列出可用工具
  /history           查看本次会话的对话记录
  /clear             清空当前会话上下文
  /session <名称>    切换 / 新建会话
  /save              手动保存会话
  /exit              退出

使用示例：
  审查 examples/buggy_sample.py
  解释 examples/clean_sample.py
  写一个函数：把秒数格式化成 1h2m3s
  为 examples/buggy_sample.py 生成单元测试
  重构 examples/buggy_sample.py
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
    parser.add_argument(
        "--mode",
        choices=["auto", "review", "explain", "generate", "test", "refactor", "ask"],
        default=None,
        help=(
            "任务模式：review 代码审查 / explain 代码解释 / generate 代码生成 / "
            "test 测试生成 / refactor 重构建议 / ask 通用问答；auto 为自动识别（默认）"
        ),
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
    parser.add_argument("--export", metavar="PATH", help="导出本次结果及轨迹到 .md 或 .json 文件（一次性任务）")
    parser.add_argument("-q", "--quiet", action="store_true", help="不显示执行轨迹")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示模型中间输出")
    parser.add_argument("--show-tools", action="store_true", help="列出可用工具后退出")
    parser.add_argument(
        "--check-api",
        action="store_true",
        help="发送一次最小请求，验证 API Key、接口地址与模型名是否可用",
    )
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
    mode = resolve_mode(getattr(args, "mode", None))
    memory = ConversationMemory.load(
        session_id=args.session,
        session_dir=config.session_dir,
        system_prompt=build_system_prompt(mode),
        autosave=True,
    )
    if args.new_session:
        memory.clear()
    agent = CodeAgent(
        config=config,
        client=create_client(config),
        registry=build_default_registry(config),
        memory=memory,
        on_event=_make_printer(args.quiet, args.verbose),
    )
    # 历史会话里可能存着旧的系统提示词，这里统一同步到当前模式
    agent.set_mode(mode)
    return agent


def _print_answer(answer: str) -> None:
    print()
    print(answer)
    print()


def _check_api(config: AgentConfig) -> int:
    """自检：发一次最小请求，确认 API 配置可用。"""

    if not config.is_online:
        print("当前处于离线模式：没有检测到 API Key。")
        print()
        print("请任选一种方式配置：")
        print("  1. 编辑项目根目录下的 .env 文件，填好 CODE_AGENT_API_KEY 后保存")
        print("  2. 在终端执行  set CODE_AGENT_API_KEY=你的密钥  （仅当前窗口有效）")
        print("  3. 启动时直接传参  python main.py --api-key 你的密钥 ...")
        return 1

    print(f"provider = {config.active_provider}")
    print(f"base_url = {config.base_url}")
    print(f"model    = {config.model}")
    print(f"api_key  = {config.masked_key()}")
    print()
    print("正在发送测试请求……")

    try:
        client = create_client(config)
        started = time.perf_counter()
        response = client.chat([{"role": "user", "content": "ping"}])
        elapsed = round(time.perf_counter() - started, 2)
    except LLMError as exc:
        print(f"✗ 调用失败：{exc}")
        print()
        print("排查建议：")
        print("- 401 / 403：API Key 无效或没有该模型的权限")
        print("- 404：接口地址或模型名不对，检查 CODE_AGENT_BASE_URL / CODE_AGENT_MODEL")
        print("- 网络错误：确认代理是否开启，必要时给终端设置 HTTP_PROXY / HTTPS_PROXY")
        return 1

    print(f"✓ API 可用，往返耗时 {elapsed}s")
    print(f"模型返回：{(response.content or '(空)')[:200]}")
    print()
    print("接下来可以直接运行，例如：")
    print('  python main.py "审查 examples/buggy_sample.py"')
    return 0


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
    elif name == "/mode":
        if len(parts) < 2:
            print(f"当前模式：{agent.mode}")
            print("可用模式：")
            for item in list_modes():
                print(f"  - {item.name:<9} {item.label} —— {item.description}")
            print(f"  - {'auto':<9} 自动识别（默认）")
            print()
            return True
        requested = parts[1].strip().lower()
        if requested != AUTO_MODE and resolve_mode(requested) == AUTO_MODE:
            print(f"未知模式：{parts[1]}，输入 /mode 查看可用模式。\n")
            return True
        print(f"已切换到模式：{agent.set_mode(requested)}\n")
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
            system_prompt=build_system_prompt(agent.mode),
            autosave=True,
        )
        agent.set_mode(agent.mode)
        print(f"已切换到会话 `{agent.memory.session_id}`（{agent.memory.turns()} 轮历史）。\n")
    else:
        print(f"未知命令：{command}，输入 /help 查看可用命令。\n")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.export and not args.prompt:
        parser.error("--export 需要同时提供任务描述")
    if args.export and Path(args.export).suffix.lower() not in (".md", ".json"):
        parser.error("--export 仅支持 .md 或 .json 文件")
    # JSON 输出必须能直接被其他程序读取，不能混入启动横幅或执行日志。
    if args.json:
        args.quiet = True

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

    if args.check_api:
        return _check_api(config)

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
        print(
            f"provider={config.active_provider}  model={config.model}  "
            f"mode={agent.mode}  workspace={config.workspace}"
        )
        if config.active_provider == "mock":
            print("提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。")
            print("      想接入真实大模型：编辑项目里的 .env 填入 API Key，再运行 python main.py --check-api 自检。")
        else:
            print(f"提示：已接入真实大模型，API Key {config.masked_key()}。")

    # ---------------------------------------------------------- 一次性任务
    if args.prompt:
        task = build_user_prompt(" ".join(args.prompt).strip(), args.file)
        result = agent.run(task)
        if args.export:
            try:
                export_report(Path(args.export), result, task, config.active_provider, config.model)
            except (OSError, ValueError) as exc:
                print(f"报告导出失败：{exc}", file=sys.stderr)
                return 2
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
