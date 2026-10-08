"""Agent 主循环：输入 → 推理 → 工具调用 → 输出。

循环的终止条件：

* 模型返回了不带工具调用的消息（正常完成）；
* 达到 ``max_iterations``（防止无限循环，返回中间结果与提示）；
* LLM 调用失败（返回可读的错误说明与排查建议）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import AgentConfig
from .llm import LLMError, create_client
from .memory import ConversationMemory
from .modes import AUTO_MODE, build_system_prompt, detect_mode, resolve_mode
from .tools import ToolRegistry, build_default_registry


@dataclass
class AgentStep:
    """Agent 执行过程中的一步，用于在界面上展示可观测的执行轨迹。"""

    kind: str  # llm / tool / error / note
    title: str
    detail: str = ""
    ok: bool = True


@dataclass
class AgentResult:
    answer: str
    steps: List[AgentStep] = field(default_factory=list)
    iterations: int = 0
    stopped: str = "final"  # final / max_iterations / error
    mode: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "iterations": self.iterations,
            "stopped": self.stopped,
            "mode": self.mode,
            "steps": [
                {"kind": step.kind, "title": step.title, "detail": step.detail, "ok": step.ok}
                for step in self.steps
            ],
        }


class CodeAgent:
    """把 LLM、工具、记忆串起来的 Agent。"""

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        client: Optional[Any] = None,
        registry: Optional[ToolRegistry] = None,
        memory: Optional[ConversationMemory] = None,
        on_event: Optional[Callable[[AgentStep], None]] = None,
        system_prompt: Optional[str] = None,
        mode: str = AUTO_MODE,
    ) -> None:
        self.config = config or AgentConfig()
        self.mode = resolve_mode(mode)
        prompt = system_prompt or build_system_prompt(self.mode)
        self.client = client or create_client(self.config)
        self.registry = registry or build_default_registry(self.config)
        if memory is None:
            self.memory = ConversationMemory(
                session_id="default",
                session_dir=self.config.session_dir,
                system_prompt=prompt,
                autosave=True,
            )
        else:
            self.memory = memory
        self.on_event = on_event or (lambda step: None)

    # ------------------------------------------------------------------ 模式
    def set_mode(self, mode: str) -> str:
        """切换任务模式，并同步更新记忆中的系统提示词。"""

        self.mode = resolve_mode(mode)
        self.memory.set_system_prompt(build_system_prompt(self.mode))
        return self.mode

    # ------------------------------------------------------------------ 主循环
    def run(self, user_input: str) -> AgentResult:
        text = (user_input or "").strip()
        if not text:
            return AgentResult(answer="请输入需要处理的内容。", stopped="error")

        effective_mode = self.mode if self.mode != AUTO_MODE else detect_mode(text)
        payload = text if self.mode == AUTO_MODE else f"[mode:{self.mode}]\n{text}"
        self.memory.add({"role": "user", "content": payload})
        steps: List[AgentStep] = []

        for iteration in range(1, self.config.max_iterations + 1):
            try:
                response = self.client.chat(self.memory.messages(), self.registry.specs())
            except LLMError as exc:
                step = AgentStep(kind="error", title="LLM 调用失败", detail=str(exc), ok=False)
                steps.append(step)
                self.on_event(step)
                answer = self._format_llm_error(exc)
                self.memory.add({"role": "assistant", "content": answer})
                return AgentResult(
                    answer=answer,
                    steps=steps,
                    iterations=iteration,
                    stopped="error",
                    mode=effective_mode,
                )

            if response.content:
                step = AgentStep(kind="llm", title="模型输出", detail=response.content)
                steps.append(step)
                self.on_event(step)

            if not response.tool_calls:
                answer = response.content or "（模型没有返回内容，请重试或换一种说法）"
                self.memory.add({"role": "assistant", "content": answer})
                return AgentResult(
                    answer=answer,
                    steps=steps,
                    iterations=iteration,
                    stopped="final",
                    mode=effective_mode,
                )

            api_calls = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in response.tool_calls
            ]
            self.memory.add(
                {"role": "assistant", "content": response.content or "", "tool_calls": api_calls}
            )

            for call in response.tool_calls:
                result = self.registry.execute(call.name, call.arguments)
                ok = bool(result.get("ok", True))
                step = AgentStep(
                    kind="tool",
                    title=f"调用工具 {call.name}",
                    detail=self._preview(call.name, call.arguments, result),
                    ok=ok,
                )
                steps.append(step)
                self.on_event(step)
                self.memory.add(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        answer = "\n".join(
            [
                f"已达到最大迭代次数（{self.config.max_iterations}），任务尚未收敛。",
                "",
                "已执行的步骤：",
                *[f"- {step.title}：{step.detail}" for step in steps if step.kind == "tool"],
                "",
                "建议：缩小任务范围（例如只审查单个文件），或用 `--max-iterations` 提高上限。",
            ]
        )
        # 尽力而为：再要一次「不带工具」的总结，避免迭代上限把已有成果全部丢掉
        forced = self._force_final_answer()
        if forced:
            answer = forced
            steps.append(AgentStep(kind="note", title="迭代上限后强制总结", detail="已禁用工具调用，仅汇总已有结果"))
            self.on_event(steps[-1])
        self.memory.add({"role": "assistant", "content": answer})
        return AgentResult(
            answer=answer,
            steps=steps,
            iterations=self.config.max_iterations,
            stopped="max_iterations",
            mode=effective_mode,
        )

    def _force_final_answer(self) -> str:
        """迭代上限到达后，让模型基于已有工具结果直接给出结论（不再允许调用工具）。

        返回空字符串表示这次总结也失败，调用方会退回「步骤摘要」。
        """

        hint = {
            "role": "user",
            "content": "已达到本轮最大迭代次数。请直接基于上面已经获得的工具结果给出最终结论，不要再请求调用工具。",
        }
        try:
            response = self.client.chat(self.memory.messages() + [hint], tools=None)
        except LLMError as exc:
            self.on_event(
                AgentStep(kind="error", title="强制总结失败", detail=str(exc), ok=False)
            )
            return ""
        # 离线规则引擎在无工具时会返回「计划」而不是结论，这种情况下退回步骤摘要
        if response.tool_calls or not response.content.strip():
            return ""
        return response.content.strip()

    # ------------------------------------------------------------------ 展示
    @staticmethod
    def _preview(name: str, arguments: Dict[str, Any], result: Dict[str, Any]) -> str:
        target = arguments.get("path") or arguments.get("query") or ""
        if not result.get("ok", True):
            return f"{target} → 失败：{result.get('error', '未知错误')}"
        if name == "read_file":
            return (
                f"{result.get('path', target)} → 读取 {result.get('start_line', 1)}-"
                f"{result.get('end_line', 0)} 行 / 共 {result.get('total_lines', 0)} 行"
            )
        if name == "analyze_code":
            summary = result.get("severity_summary") or {}
            return (
                f"{result.get('path', target)} → 评分 {result.get('score', '-')}，"
                f"问题 {summary.get('total', 0)} 项"
                f"（高 {summary.get('high', 0)} / 中 {summary.get('medium', 0)} / 低 {summary.get('low', 0)}）"
            )
        if name == "list_dir":
            return f"{result.get('root', target)} → {result.get('count', 0)} 项"
        if name == "search_in_files":
            return f"`{result.get('query', target)}` → 命中 {result.get('count', 0)} 处"
        if name == "run_python":
            return (
                f"{result.get('path', target)} → 退出码 {result.get('returncode')}，"
                f"耗时 {result.get('duration_ms', 0)} ms"
            )
        if name == "write_file":
            return f"{result.get('path', target)} → 写入 {result.get('bytes', 0)} 字节"
        return json.dumps(result, ensure_ascii=False)[:200]

    def _format_llm_error(self, error: LLMError) -> str:
        message = str(error)
        hints: List[str] = []
        if "HTTP 401" in message or "HTTP 403" in message:
            hints.append("API Key 无效或没有该模型的权限，请检查 `CODE_AGENT_API_KEY`。")
        elif "HTTP 404" in message:
            hints.append("接口地址或模型名可能不正确，请检查 `CODE_AGENT_BASE_URL` 与 `CODE_AGENT_MODEL`。")
        elif "HTTP 429" in message:
            hints.append("触发限流，稍后重试或降低调用频率。")
        elif "HTTP" not in message:
            hints.append("网络不可达时，可用 `--provider mock` 切换到离线模式体验完整流程。")
        hints.append("也可以用 `--provider mock` 在本地无网络环境下完成 Agent 循环演示。")
        return "\n".join([f"调用大模型失败：{message}", "", "排查建议：", *[f"- {hint}" for hint in hints]])
