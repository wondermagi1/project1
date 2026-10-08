"""任务模式：让同一个 Agent 支持不同的代码处理任务。

Agent 的系统提示词 = 公共规则（``prompts.BASE_SYSTEM_PROMPT``）+ 当前模式指令。
模式既可以由用户显式指定（``--mode refactor``），也可以根据输入自动识别。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .prompts import BASE_SYSTEM_PROMPT

AUTO_MODE = "auto"
DEFAULT_MODE = "review"


@dataclass(frozen=True)
class TaskMode:
    """一个任务模式的定义。"""

    name: str
    label: str
    direction: str
    description: str
    keywords: Tuple[str, ...]
    instructions: str
    output_format: str
    suggested_tools: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "direction": self.direction,
            "description": self.description,
            "suggested_tools": list(self.suggested_tools),
        }


MODES: Dict[str, TaskMode] = {
    "review": TaskMode(
        name="review",
        label="代码审查",
        direction="代码审查 Agent",
        description="分析代码质量、发现潜在 Bug、给出改进建议",
        keywords=("审查", "评审", "检查代码", "代码质量", "找 bug", "找bug", "review", "audit"),
        instructions=(
            "先读目标文件，再调用 analyze_code 获取客观问题清单；"
            "对静态规则覆盖不到的可疑逻辑（例如参数声明了却没用、分支写错），"
            "用 run_python 写最小实验去验证，不要只凭印象下结论。"
        ),
        output_format=(
            "1. 结论与评分（0-100，说明扣分依据）\n"
            "2. 问题清单表格：严重度 | 位置 | 问题 | 影响 | 建议\n"
            "3. 优先修复顺序（说明先修哪一条、为什么）\n"
            "4. 必要时给出修改后的示例代码"
        ),
        suggested_tools=("read_file", "analyze_code", "run_python"),
    ),
    "explain": TaskMode(
        name="explain",
        label="代码解释",
        direction="代码解释 Agent",
        description="解释代码逻辑、生成注释、回答代码问题",
        keywords=("解释", "讲解", "说明", "看懂", "注释", "什么意思", "explain", "docstring"),
        instructions=(
            "先读文件，用 analyze_code 获取结构与规模，再按执行顺序讲清代码在做什么、"
            "数据如何流动、有哪些隐含前提。需要补充注释时，用 diff_files 展示建议的 docstring / 注释改动。"
        ),
        output_format=(
            "1. 一句话概括这个文件/模块的职责\n"
            "2. 结构概览（类、函数、各自职责与调用关系）\n"
            "3. 执行流程与数据流（按顺序说明）\n"
            "4. 容易误解或出错的地方\n"
            "5. 建议补充的注释（用 diff 形式给出）"
        ),
        suggested_tools=("read_file", "analyze_code", "diff_files"),
    ),
    "generate": TaskMode(
        name="generate",
        label="代码生成",
        direction="代码生成 Agent",
        description="根据自然语言描述生成代码片段",
        keywords=("生成代码", "写一个", "写个", "实现一个", "实现个", "帮我写", "补全", "generate", "implement"),
        instructions=(
            "先确认需求中的输入、输出、边界条件与依赖约束；如果工作区已有相似实现，"
            "先用 search_in_files / list_dir 了解命名风格与目录约定。"
            "用 write_file 写文件（默认不覆盖已有文件），写完必须用 run_python 或 run_tests 验证能正常运行。"
        ),
        output_format=(
            "1. 需求理解（输入、输出、边界条件）\n"
            "2. 生成的完整代码（含类型标注与 docstring）\n"
            "3. 文件写入路径与验证命令、执行结果\n"
            "4. 未覆盖的边界与后续建议"
        ),
        suggested_tools=("list_dir", "search_in_files", "write_file", "run_python"),
    ),
    "test": TaskMode(
        name="test",
        label="测试生成",
        direction="测试生成 Agent",
        description="根据代码自动生成单元测试用例",
        keywords=("测试用例", "单元测试", "生成测试", "写测试", "补测试", "pytest", "unittest"),
        instructions=(
            "先读文件并用 analyze_code 找出公开函数与分支；生成 unittest 测试文件（write_file，"
            "文件放在被测文件同目录，命名 test_<原名>.py），然后必须用 run_tests 真正运行。"
            "如果用例失败，分析原因并修复后重跑，最多迭代两轮，最后如实报告通过/失败数量。"
        ),
        output_format=(
            "1. 待覆盖目标表格：符号 | 位置 | 覆盖的正常/边界/异常用例\n"
            "2. 生成的测试文件路径 + 完整代码\n"
            "3. run_tests 的真实执行结果（用例数、通过数、失败项）\n"
            "4. 尚未覆盖的分支与建议"
        ),
        suggested_tools=("read_file", "analyze_code", "write_file", "run_tests"),
    ),
    "refactor": TaskMode(
        name="refactor",
        label="重构建议",
        direction="重构建议 Agent",
        description="分析代码坏味道、给出重构建议",
        keywords=("重构", "坏味道", "优化代码", "拆分函数", "代码异味", "refactor", "smell"),
        instructions=(
            "先读文件并用 analyze_code 定位坏味道，再按「安全类 / 结构类 / 可读性类」组织重构方案。"
            "每一条都要给出改动前后的对比，用 diff_files 展示具体差异；"
            "重构后的完整版本写到 <原名>_refactored.py（这是生成产物，已存在时可直接 overwrite=true 覆盖），"
            "但绝不修改原文件。"
        ),
        output_format=(
            "1. 坏味道清单（按类别分组，标注位置与危害）\n"
            "2. 重构方案：每条含 why（为什么改）、how（怎么改）、diff（前后对比）\n"
            "3. 建议的实施顺序（哪些可以立即改、哪些需要回归测试）\n"
            "4. 重构后版本的写入路径与验证命令"
        ),
        suggested_tools=("read_file", "analyze_code", "diff_files", "write_file"),
    ),
    "ask": TaskMode(
        name="ask",
        label="通用问答",
        direction="其他（项目结构 / 检索 / 运行验证）",
        description="回答代码相关问题，必要时先查证",
        keywords=(),
        instructions=(
            "先判断问题需不需要查证：涉及具体文件时先读文件，涉及全局约定时先搜索或列目录，"
            "涉及运行结果时直接跑一次。结论要基于工具返回的真实内容。"
        ),
        output_format="先给直接答案，再补充依据（文件、行号或运行输出）。",
        suggested_tools=("list_dir", "search_in_files", "read_file"),
    ),
}

# 自动识别顺序：先判断特征最明确的模式
DETECTION_ORDER = ("test", "generate", "refactor", "explain", "review")

MODE_LINE_RE = re.compile(r"\[mode:([a-z]+)\]")


def list_modes() -> List[TaskMode]:
    """返回所有模式（用于 CLI 展示）。"""

    return [MODES["review"], MODES["explain"], MODES["generate"], MODES["test"], MODES["refactor"], MODES["ask"]]


def resolve_mode(mode: Optional[str]) -> str:
    """把用户输入的模式名规范化；未知值回退到 ``auto``。"""

    if not mode:
        return AUTO_MODE
    name = str(mode).strip().lower()
    if name == AUTO_MODE:
        return AUTO_MODE
    return name if name in MODES else AUTO_MODE


def detect_mode(text: str) -> str:
    """根据自然语言自动识别模式，识别不出时按代码审查处理。"""

    lowered = (text or "").lower()
    for name in DETECTION_ORDER:
        mode = MODES[name]
        if any(keyword in text for keyword in mode.keywords) or any(
            keyword in lowered for keyword in mode.keywords if keyword.isascii()
        ):
            return name
    return DEFAULT_MODE


def extract_mode_marker(text: str) -> Optional[str]:
    """从用户消息里取出 CLI 注入的模式标记。"""

    match = MODE_LINE_RE.search(text or "")
    return match.group(1) if match else None


def strip_mode_marker(text: str) -> str:
    return MODE_LINE_RE.sub("", text or "").strip()


def build_system_prompt(mode: str = AUTO_MODE) -> str:
    """拼装完整的系统提示词。"""

    mode = resolve_mode(mode)
    if mode == AUTO_MODE:
        catalog = "\n".join(
            f"- {item.name}（{item.label}）：{item.description}" for item in list_modes()
        )
        return (
            BASE_SYSTEM_PROMPT
            + "\n\n## 自动模式\n"
            + "先判断用户想要哪种任务，再按其要求执行：\n"
            + catalog
            + "\n\n用户显式指定模式时，以指定模式为准。"
        )

    detail = MODES[mode]
    return (
        f"{BASE_SYSTEM_PROMPT}\n\n"
        f"## 当前任务模式：{detail.label}（{detail.name}）\n"
        f"对应方向：{detail.direction}\n"
        f"目标：{detail.description}\n\n"
        f"### 执行要求\n{detail.instructions}\n\n"
        f"### 输出规范\n{detail.output_format}\n\n"
        f"优先考虑使用这些工具：{', '.join(detail.suggested_tools)}。"
    )
