"""极简 Web 界面：只用标准库的 HTTP 服务 + 单页聊天。

复用现有的 ``CodeAgent`` / 工具 / 记忆，因此网页版与命令行版行为完全一致。
页面模板放在同目录的 ``webui.html``，便于单独调整样式与交互。

安全默认：只监听 ``127.0.0.1``，避免把「可执行代码」的 Agent 暴露到局域网。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

from .agent import AgentStep, CodeAgent
from .config import AgentConfig
from .llm import create_client
from .memory import ConversationMemory, safe_session_id
from .reporting import build_report
from .project import ProjectTools, MAX_FILE_BYTES
from .modes import build_system_prompt, list_modes, resolve_mode
from .tools import build_default_registry

UPLOAD_DIR = "uploads"
MAX_UPLOAD_BYTES = 2_000_000
MAX_REQUEST_BYTES = 12_100_000  # 允许 JSON 对中文及控制字符转义后的大小
_SESSION_LOCKS = [threading.Lock() for _ in range(64)]
_SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]")
_HTML_CACHE: Optional[str] = None


def _safe_upload_name(name: str) -> str:
    """把上传的文件名收敛成安全、可控的名字（只保留文件名，去掉路径与危险字符）。"""

    cleaned = Path(str(name or "")).name.strip()
    if not cleaned or cleaned in (".", ".."):
        raise ValueError("文件名不能为空")
    cleaned = _SAFE_NAME.sub("_", cleaned).strip("._")
    if not cleaned:
        raise ValueError("文件名不合法")
    return cleaned[:120]


def _load_html() -> str:
    """读取页面模板（首次读取后缓存）。"""

    global _HTML_CACHE
    if _HTML_CACHE is None:
        template = Path(__file__).with_name("webui.html")
        _HTML_CACHE = template.read_text(encoding="utf-8")
    return _HTML_CACHE


def _make_agent(
    config: AgentConfig,
    session_id: str,
    mode: str,
    on_event: Callable[[AgentStep], None],
) -> CodeAgent:
    memory = ConversationMemory.load(
        session_id=session_id,
        session_dir=config.session_dir,
        system_prompt=build_system_prompt(mode),
        autosave=True,
    )
    agent = CodeAgent(
        config=config,
        client=create_client(config),
        registry=build_default_registry(config),
        memory=memory,
        on_event=on_event,
    )
    agent.set_mode(mode)
    return agent


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # 由 serve() 注入
    config: AgentConfig

    def log_message(self, fmt: str, *args: Any) -> None:  # 静音访问日志
        return

    # ------------------------------------------------------------------ 工具
    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        try:
            body = _load_html().encode("utf-8")
        except OSError as exc:
            self._send_json({"error": f"页面模板读取失败：{exc}"}, status=500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _config_for(self, provider: str) -> AgentConfig:
        """按前端选择覆盖模型来源（auto / openai / mock），网络不稳时可切离线。"""

        name = str(provider or "auto").strip().lower()
        if name == "auto":
            return self.config
        if name in ("auto", "openai", "mock") and name != self.config.provider:
            return replace(self.config, provider=name)
        return self.config

    def _run_agent(
        self,
        message: str,
        mode: str,
        session: str,
        emit: Callable[[AgentStep], None],
        provider: str = "auto",
    ):
        config = self._config_for(provider)
        # 固定数量的锁避免无限增长，同名会话串行处理，防止覆盖对话文件。
        key = (str(config.session_dir), safe_session_id(session or "web"))
        with _SESSION_LOCKS[hash(key) % len(_SESSION_LOCKS)]:
            agent = _make_agent(config, session or "web", resolve_mode(mode), emit)
            return agent.run(message)

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        assets = {"/static/workbench.js": ("workbench.js", "text/javascript"),
                  "/static/workbench.css": ("workbench.css", "text/css")}
        if parsed.path in assets:
            name, content_type = assets[parsed.path]
            body = (Path(__file__).parent / "static" / name).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/":
            self._send_html()
            return

        if parsed.path == "/api/status":
            self._send_json(
                {
                    "provider": self.config.active_provider,
                    "model": self.config.model if self.config.is_online else "offline-rule-engine",
                    "workspace": str(self.config.workspace),
                    "api_key_masked": self.config.masked_key(),
                    "max_iterations": self.config.max_iterations,
                    "read_only": self.config.read_only,
                    "allow_exec": self.config.allow_exec and not self.config.read_only,
                    "tools": [tool["name"] for tool in build_default_registry(self.config).describe()],
                    "modes": [item.to_dict() for item in list_modes()],
                }
            )
            return

        if parsed.path == "/api/project":
            self._send_json(ProjectTools(self.config).inventory())
            return
        if parsed.path == "/api/sessions":
            self._send_json({"sessions": ConversationMemory.list_sessions(self.config.session_dir)})
            return
        query = parse_qs(parsed.query)
        if parsed.path == "/api/history":
            name = query.get("session", ["web"])[0]
            memory = ConversationMemory.load(name, self.config.session_dir)
            messages = [{"role": row["role"], "content": str(row.get("content") or "")} for row in memory.messages() if row.get("role") in ("user", "assistant") and row.get("content") and not row.get("tool_calls")]
            self._send_json({"session": memory.session_id, "messages": messages})
            return
        if parsed.path == "/api/file":
            try:
                path = ProjectTools(self.config).resolve_file(query.get("path", [""])[0])
                with path.open("rb") as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
                    raise ValueError("文件过大或不是文本，无法预览")
                self._send_json({"path": path.relative_to(self.config.workspace).as_posix(), "content": raw.decode("utf-8-sig")})
            except (ValueError, OSError, UnicodeError):
                self._send_json({"error": "无法预览：文件不存在、非 UTF-8 文本或超出允许范围"}, status=400)
            return
        if parsed.path == "/api/chat/stream":
            self._send_json({"error": "请使用 POST /api/chat/stream"}, status=405)
            return
        self._send_json({"error": "not found"}, status=404)

    def _stream(self, payload: Dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # SSE 没有 Content-Length，用 Connection: close 让响应有明确结束点
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(step: AgentStep) -> None:
            try:
                data = json.dumps(
                    {"kind": step.kind, "title": step.title, "detail": step.detail, "ok": step.ok},
                    ensure_ascii=False,
                )
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        try:
            result = self._run_agent(
                payload.get("message", ""),
                payload.get("mode", "auto"),
                payload.get("session", ""),
                emit,
                payload.get("provider", "auto"),
            )
        except Exception as exc:  # noqa: BLE001 —— 把异常反馈给前端，而不是断开连接
            final: Dict[str, Any] = {"kind": "error", "detail": f"{type(exc).__name__}: {exc}"}
        else:
            final = {
                "kind": "final",
                "answer": result.answer,
                "mode": result.mode,
                "stopped": result.stopped,
                "iterations": result.iterations,
                "report": build_report(result, payload.get("message", ""), self._config_for(payload.get("provider", "auto")).active_provider, self.config.model),
            }
        try:
            self.wfile.write(
                f"event: done\ndata: {json.dumps(final, ensure_ascii=False)}\n\n".encode("utf-8")
            )
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.close_connection = True
            self._send_json({"error": "不接受跨站请求"}, status=403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if self.headers.get("Transfer-Encoding") or length < 0 or length > MAX_REQUEST_BYTES:
            self.close_connection = True
            self._send_json({"error": "请求长度无效或超过上限"}, status=413 if length > MAX_REQUEST_BYTES else 400)
            return
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._send_json({"error": "invalid json"}, status=400)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "invalid payload"}, status=400)
            return

        if parsed.path == "/api/upload":
            try:
                self._handle_upload(payload)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            except OSError:
                self._send_json({"error": "文件保存失败，请检查目录权限"}, status=500)
            return

        if parsed.path in ("/api/project/scan", "/api/project/git", "/api/project/tests"):
            tool = {"/api/project/scan": "project_scan", "/api/project/git": "git_diff", "/api/project/tests": "run_tests"}[parsed.path]
            arguments = payload if tool != "run_tests" else {"path": str(payload.get("path") or "tests"), "timeout": 120}
            result = build_default_registry(self.config).execute(tool, arguments)
            self._send_json(result, status=200 if result.get("ok") else 400)
            return

        if parsed.path not in ("/api/chat", "/api/chat/stream"):
            self._send_json({"error": "not found"}, status=404)
            return
        if not isinstance(payload.get("message"), str) or not payload["message"].strip():
            self._send_json({"error": "请输入任务内容"}, status=400)
            return
        for key, default in (("mode", "auto"), ("session", "web"), ("provider", "auto")):
            if not isinstance(payload.get(key, default), str):
                self._send_json({"error": f"{key} 必须是字符串"}, status=400)
                return
        if parsed.path == "/api/chat/stream":
            self._stream(payload)
            return

        try:
            result = self._run_agent(
                payload["message"], payload.get("mode", "auto"), payload.get("session", "web"),
                lambda step: None, payload.get("provider", "auto"),
            )
        except Exception:  # 服务边界兜底，保持 JSON 协议
            logging.exception("Web task failed")
            self._send_json({"error": "任务执行失败，请检查模型配置及工作区权限"}, status=500)
            return
        self._send_json(result.to_dict())

    def _handle_upload(self, payload: Dict[str, Any]) -> None:
        """把上传的代码文件写进工作区 uploads/ 目录，返回相对路径。"""

        if self.config.read_only:
            self._send_json({"error": "只读模式禁止上传写入"}, status=403)
            return
        filename = _safe_upload_name(str(payload.get("filename") or ""))
        content = payload.get("content")
        if content is None:
            raise ValueError("缺少 content 字段")
        if not isinstance(content, str):
            raise ValueError("content 必须是文本")
        if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
            self._send_json({"error": "文件超过 2MB 上限"}, status=413)
            return

        upload_root = (self.config.workspace / UPLOAD_DIR).resolve()
        if not upload_root.is_relative_to(self.config.workspace):
            raise ValueError("上传目录超出工作区")
        upload_root.mkdir(parents=True, exist_ok=True)
        target = (upload_root / filename).resolve()
        if target != upload_root and upload_root not in target.parents:
            self._send_json({"error": "文件名不合法"}, status=400)
            return
        # 同名上传保留旧文件，使用排他创建防止并发覆盖。
        while True:
            try:
                with target.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
                break
            except FileExistsError:
                target = upload_root / f"{Path(filename).stem}_{uuid4().hex[:8]}{Path(filename).suffix}"
        rel = str(target.relative_to(self.config.workspace)).replace("\\", "/")
        self._send_json(
            {
                "path": rel,
                "bytes": len(content.encode("utf-8")),
                "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
            }
        )


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False,
          provider: str = "auto", read_only: bool = False, allow_exec: bool = True,
          workspace: Optional[str] = None) -> None:
    """启动 Web 服务。"""

    config = AgentConfig.from_env(workspace=Path(workspace) if workspace else Path.cwd(), provider=provider,
                                  read_only=read_only, allow_exec=allow_exec and not read_only)
    _Handler.config = config  # 注入给所有请求

    server = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}/"
    print(f"代码助手 Agent Web 界面已启动：{url}")
    print(f"provider={config.active_provider}  model={config.model}  workspace={config.workspace}")
    print("按 Ctrl+C 停止。")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
