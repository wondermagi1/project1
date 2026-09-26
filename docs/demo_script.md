# 演示视频脚本（1 分钟以内，可选提交物）

> 视频为可选项。若录制，建议横屏 1080p、字号放大到 16pt 以上，提前清屏以便看清轨迹。

## 录制前准备

```bash
cd code-assistant-agent
clear            # Windows: cls
```

## 分镜脚本（总时长约 55 秒）

| 时间 | 画面 | 旁白 |
| --- | --- | --- |
| 0:00–0:07 | 展示 README 的架构图与项目结构 | 「这是我完成的代码助手 Agent，用 Python 标准库实现，核心是输入、推理、工具调用、输出的完整循环。」 |
| 0:07–0:15 | 终端执行 `python main.py --show-tools` | 「Agent 内置 6 个工具：读文件、列目录、搜索代码、静态分析、运行代码、写文件，每个工具都有 JSON Schema 描述和安全约束。」 |
| 0:15–0:30 | 执行 `python main.py --provider mock "审查 examples/buggy_sample.py"` | 「这里用离线模式演示，不需要 API Key。Agent 先读取文件，再调用静态分析工具，最后生成带行号、严重度和修复建议的审查报告。」 |
| 0:30–0:38 | 用方向键指向表格中的 `eval`、裸 `except`、可变默认参数三行 | 「它准确找出了 eval 动态执行、shell 命令注入、裸 except 这三个高危问题，每条结论都对应真实行号。」 |
| 0:38–0:47 | 执行 `python main.py --provider mock "审查 examples/clean_sample.py"` | 「换成规范代码，评分是满分 100，说明规则不是无差别报错，而是有明确依据。」 |
| 0:47–0:55 | 执行 `python main.py`，输入 `为 examples/buggy_sample.py 生成单元测试` 与 `/tools` | 「同一套架构还支持代码解释和测试生成，并支持多轮会话记忆；接上真实模型只需配置 API Key 和 --provider openai。」 |

## 备用：有 API Key 时的演示命令

```bash
export CODE_AGENT_API_KEY=sk-xxxx     # Windows: $env:CODE_AGENT_API_KEY = "sk-xxxx"
python main.py --provider openai --model gpt-4o-mini "审查 examples/buggy_sample.py"
```

此时大模型会基于 `read_file` 与 `analyze_code` 返回的真实内容组织语言，可以在报告后追加修复后的代码示例。

## 录制检查清单

- 视频不超过 1 分钟，能看清命令行文字。
- 至少展示一次完整的工具调用轨迹（`[ok] read_file ...`）。
- 至少展示一次真实结论（含行号或评分）。
- 若使用在线模型，提前确认额度与网络，避免现场卡住。
