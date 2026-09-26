"""对话记忆：保存多轮上下文，支持持久化与自动裁剪。

设计要点：

* 会话以 JSON 文件保存，进程重启后可继续对话。
* 自动裁剪时保证「每个回合从 user 消息开始」，不会产生孤立的 tool 结果，
  否则再次发给 OpenAI 兼容接口会因消息结构非法而报错。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SAFE_ID = re.compile(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+")


def safe_session_id(raw: str) -> str:
    """把会话名转成安全的文件名。"""

    cleaned = _SAFE_ID.sub("_", (raw or "").strip())
    return cleaned or "default"


class ConversationMemory:
    """维护一条消息列表，并提供持久化能力。"""

    def __init__(
        self,
        session_id: str = "default",
        session_dir: Optional[Path] = None,
        system_prompt: str = "",
        max_messages: int = 60,
        autosave: bool = False,
    ) -> None:
        self.session_id = safe_session_id(session_id)
        self.session_dir = Path(session_dir) if session_dir else None
        self.system_prompt = system_prompt
        self.max_messages = max(8, int(max_messages))
        self.autosave = autosave
        self._messages: List[Dict[str, Any]] = []
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

    # ------------------------------------------------------------------ 读写
    @property
    def path(self) -> Optional[Path]:
        if self.session_dir is None:
            return None
        return Path(self.session_dir) / f"{self.session_id}.json"

    def messages(self) -> List[Dict[str, Any]]:
        """返回消息副本，避免调用方意外修改内部状态。"""

        return [dict(message) for message in self._messages]

    def add(self, message: Dict[str, Any]) -> None:
        self._messages.append(dict(message))
        self._trim()
        if self.autosave:
            self.save()

    def extend(self, messages: List[Dict[str, Any]]) -> None:
        for message in messages:
            self._messages.append(dict(message))
        self._trim()
        if self.autosave:
            self.save()

    def clear(self) -> None:
        """清空对话，但保留 system prompt。"""

        self._messages = [m for m in self._messages if m.get("role") == "system"]
        if self.autosave:
            self.save()

    def __len__(self) -> int:
        return len(self._messages)

    # ------------------------------------------------------------------ 统计
    def turns(self) -> int:
        return sum(1 for message in self._messages if message.get("role") == "user")

    def last_user_text(self) -> str:
        for message in reversed(self._messages):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""

    def tool_result(self, name: str) -> Optional[Dict[str, Any]]:
        """返回最近一次指定工具的结构化结果（工具统一返回 JSON 字符串）。"""

        for message in reversed(self._messages):
            if message.get("role") != "tool" or message.get("name") != name:
                continue
            try:
                return json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                return {"ok": False, "error": "工具结果不是合法 JSON"}
        return None

    def transcript(self, limit: int = 20) -> List[Dict[str, str]]:
        """导出去掉 system 的对话记录，便于展示。"""

        rows: List[Dict[str, str]] = []
        for message in self._messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "tool":
                text = str(message.get("content") or "")
                rows.append({"role": f"tool:{message.get('name', '')}", "content": text[:400]})
            else:
                rows.append({"role": str(role), "content": str(message.get("content") or "")[:400]})
        return rows[-limit:]

    # ------------------------------------------------------------------ 持久化
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": len(self._messages),
            "messages": self._messages,
        }

    def save(self) -> Optional[Path]:
        target = self.path
        if target is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        # 先写临时文件再替换，避免写入中断导致会话文件损坏。
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(target)
        return target

    @classmethod
    def load(
        cls,
        session_id: str,
        session_dir: Optional[Path],
        system_prompt: str = "",
        max_messages: int = 60,
        autosave: bool = False,
    ) -> "ConversationMemory":
        memory = cls(
            session_id=session_id,
            session_dir=session_dir,
            system_prompt=system_prompt,
            max_messages=max_messages,
            autosave=autosave,
        )
        target = memory.path
        if target and target.is_file():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return memory
            messages = data.get("messages")
            if isinstance(messages, list):
                loaded = [m for m in messages if isinstance(m, dict) and m.get("role")]
                if system_prompt and not any(m.get("role") == "system" for m in loaded):
                    loaded.insert(0, {"role": "system", "content": system_prompt})
                memory._messages = loaded
                memory._trim()
        return memory

    @staticmethod
    def list_sessions(session_dir: Path) -> List[Dict[str, Any]]:
        directory = Path(session_dir)
        if not directory.is_dir():
            return []
        sessions: List[Dict[str, Any]] = []
        for item in sorted(directory.glob("*.json")):
            try:
                data = json.loads(item.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sessions.append(
                {
                    "session_id": data.get("session_id", item.stem),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": data.get("message_count", 0),
                }
            )
        return sessions

    # ------------------------------------------------------------------ 内部
    def _trim(self) -> None:
        head = [m for m in self._messages if m.get("role") == "system"]
        body = [m for m in self._messages if m.get("role") != "system"]
        limit = max(4, self.max_messages - len(head))
        body = body[-limit:]
        # 丢掉裁剪后残留在开头的 tool / assistant 消息，保证回话从 user 开始。
        while body and body[0].get("role") != "user":
            body.pop(0)
        self._messages = head + body
