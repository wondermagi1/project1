"""将单轮结果与工具轨迹导出为可提交的 Markdown 或 JSON。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .agent import AgentResult


def build_report(result: AgentResult, task: str, provider: str, model: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "provider": provider,
        "model": model if provider != "mock" else "offline-rule-engine",
        **result.to_dict(),
    }


def render_report(report: Dict[str, Any]) -> str:
    steps = report["steps"]
    tools = [step for step in steps if step["kind"] == "tool"]
    lines = [
        "# 代码助手任务报告", "",
        f"- 生成时间：{report['created_at']}",
        f"- 模型来源：{report['provider']} / {report['model']}",
        f"- 任务模式：{report['mode']}",
        f"- 结束状态：{report['stopped']}",
        f"- 模型迭代：{report['iterations']}",
        f"- 工具调用：{len(tools)} 次，失败 {sum(not step['ok'] for step in tools)} 次",
        "", "## 用户任务", "", report["task"],
        "", "## 结果", "", report["answer"], "", "## 执行轨迹", "",
    ]
    for index, step in enumerate(steps, 1):
        lines.extend([f"### {index}. {step['title']}（{'成功' if step['ok'] else '失败'}）", "", step["detail"], ""])
    if not steps:
        lines.append("本轮没有执行步骤。")
    if report["provider"] == "mock":
        lines.extend(["", "> 本报告来自离线规则引擎，用于验证流程，不代表真实大模型能力。"])
    return "\n".join(lines) + "\n"


def export_report(path: Path, result: AgentResult, task: str, provider: str, model: str) -> Path:
    path = path.expanduser()
    if path.suffix.lower() not in (".md", ".json"):
        raise ValueError("报告格式必须为 .md 或 .json")
    report = build_report(result, task, provider, model)
    content = json.dumps(report, ensure_ascii=False, indent=2) if path.suffix.lower() == ".json" else render_report(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 显式拒绝覆盖已有报告，避免丢失此前的演示证据。
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return path
