"""代码助手 Agent：一个不依赖第三方库的极简 Agent 实现。

模块划分：

* ``config``   —— 运行配置（环境变量 / 命令行）
* ``llm``      —— LLM 客户端（OpenAI 兼容接口 + 离线规则引擎）
* ``memory``   —— 多轮对话记忆与持久化
* ``tools``    —— 工具注册表与内置工具
* ``analysis`` —— 静态分析引擎
* ``agent``    —— Agent 主循环（输入 → 推理 → 工具调用 → 输出）
* ``cli``      —— 命令行交互界面
"""

from .agent import AgentResult, AgentStep, CodeAgent
from .config import AgentConfig
from .llm import LLMError, MockLLM, OpenAICompatibleClient, create_client
from .memory import ConversationMemory
from .tools import ToolRegistry, build_default_registry

__all__ = [
    "AgentConfig",
    "AgentResult",
    "AgentStep",
    "CodeAgent",
    "ConversationMemory",
    "LLMError",
    "MockLLM",
    "OpenAICompatibleClient",
    "ToolRegistry",
    "build_default_registry",
    "create_client",
]

__version__ = "1.0.0"
