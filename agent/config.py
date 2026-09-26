"""运行配置。

配置优先级：命令行参数 > 环境变量 > 默认值。

支持的环境变量：

===========================  ====================================================
``CODE_AGENT_API_KEY``       API Key（回退到 ``OPENAI_API_KEY``）
``CODE_AGENT_BASE_URL``      OpenAI 兼容接口地址（回退到 ``OPENAI_BASE_URL``）
``CODE_AGENT_MODEL``         模型名（回退到 ``OPENAI_MODEL``）
``CODE_AGENT_PROVIDER``      ``auto`` / ``openai`` / ``mock``
===========================  ====================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
ENV_PREFIX = "CODE_AGENT_"


@dataclass
class AgentConfig:
    """Agent 的全部可调参数。"""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    provider: str = "auto"
    workspace: Path = field(default_factory=Path.cwd)
    session_dir: Path = field(default_factory=lambda: Path(".agent_sessions"))
    max_iterations: int = 6
    max_retries: int = 3
    retry_backoff: float = 1.5
    temperature: float = 0.2
    request_timeout: float = 60.0
    exec_timeout: float = 15.0
    max_file_chars: int = 20000
    allow_exec: bool = True
    read_only: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        # 边界处理：非法数值一律收敛到安全范围，避免参数错误直接崩溃。
        self.workspace = Path(self.workspace).expanduser().resolve()
        self.session_dir = Path(self.session_dir).expanduser()
        if not self.session_dir.is_absolute():
            # 会话文件默认放在工作区内，便于随项目一起清理与忽略。
            self.session_dir = self.workspace / self.session_dir
        self.provider = (self.provider or "auto").strip().lower()
        if self.provider not in ("auto", "openai", "mock"):
            self.provider = "auto"
        self.max_iterations = max(1, min(int(self.max_iterations), 20))
        self.max_retries = max(1, min(int(self.max_retries), 8))
        self.max_file_chars = max(500, int(self.max_file_chars))
        self.exec_timeout = max(0.5, float(self.exec_timeout))
        self.request_timeout = max(1.0, float(self.request_timeout))

    # ------------------------------------------------------------------ 构造
    @classmethod
    def from_env(cls, **overrides: Any) -> "AgentConfig":
        """从环境变量构造配置，并用 ``overrides`` 覆盖（``None`` 表示不覆盖）。"""

        env = os.environ.get
        config = cls(
            api_key=(env(ENV_PREFIX + "API_KEY") or env("OPENAI_API_KEY") or "").strip(),
            base_url=(env(ENV_PREFIX + "BASE_URL") or env("OPENAI_BASE_URL") or DEFAULT_BASE_URL).strip(),
            model=(env(ENV_PREFIX + "MODEL") or env("OPENAI_MODEL") or DEFAULT_MODEL).strip(),
            provider=(env(ENV_PREFIX + "PROVIDER") or "auto").strip().lower(),
        )
        clean = {key: value for key, value in overrides.items() if value is not None}
        return replace(config, **clean) if clean else config

    # ------------------------------------------------------------------ 查询
    @property
    def active_provider(self) -> str:
        """实际使用的 provider：``auto`` 会按是否配置了 Key 自动选择。"""

        if self.provider in ("openai", "mock"):
            return self.provider
        return "openai" if self.api_key else "mock"

    @property
    def is_online(self) -> bool:
        return self.active_provider == "openai"

    def masked_key(self) -> str:
        if not self.api_key:
            return "(未配置)"
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"

    def describe(self) -> str:
        return (
            f"provider={self.active_provider} model={self.model} "
            f"workspace={self.workspace} api_key={self.masked_key()}"
        )

    def to_dict(self) -> Dict[str, Any]:
        data = dict(self.__dict__)
        data["workspace"] = str(data["workspace"])
        data["session_dir"] = str(data["session_dir"])
        data["api_key"] = self.masked_key()
        return data
