# 代码助手 Agent（Code Assistant Agent）

一个用 Python 标准库实现的代码助手 Agent，面向「代码审查」场景，同时支持代码解释、单元测试生成与代码运行验证。

- **零第三方依赖**：只用标准库，`Python 3.10+` 开箱即用，不需要 `pip install`。
- **完整的 Agent 循环**：输入 → 推理 → 工具调用 → 观察结果 → 输出，最多迭代 6 轮（可配置）。
- **可插拔 LLM**：支持任意 OpenAI 兼容接口（OpenAI / DeepSeek / 通义千问 compatible-mode / vLLM / Ollama）。
- **离线可用**：没有 API Key 时自动切换到内置规则引擎，依然完整走通多步工具调用，方便演示与测试。
- **6 个内置工具**：读文件、列目录、搜索代码、静态分析、运行 Python、写文件。
- **工程化细节**：多轮记忆持久化、指数退避重试、超时保护、路径越界拦截、结构化错误处理、52 个单元测试。

## 1. 快速开始

环境要求：Python 3.10 或更高版本（开发环境为 Python 3.13），无需安装任何第三方包。

```bash
cd code-assistant-agent

# 方式一：离线模式（不需要 API Key，推荐先跑这个看效果）
python main.py --provider mock "审查 examples/buggy_sample.py"

# 方式二：接入真实大模型
export CODE_AGENT_API_KEY=sk-xxxx        # Windows PowerShell: $env:CODE_AGENT_API_KEY = "sk-xxxx"
python main.py --provider openai --model gpt-4o-mini "审查 examples/buggy_sample.py"

# 方式三：交互式对话
python main.py
```

运行效果（离线模式，`examples/buggy_sample.py` 为故意写坏的样例）：

```text
provider=mock  model=gpt-4o-mini  workspace=...\code-assistant-agent
提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。
  [ok  ] read_file: examples/buggy_sample.py → 读取 1-88 行 / 共 88 行
  [ok  ] analyze_code: examples/buggy_sample.py → 评分 0，问题 19 项（高 3 / 中 3 / 低 13）

# 代码审查报告：`examples/buggy_sample.py`

**综合评分：0/100（较差，存在明显的正确性或安全风险）**

| 严重度 | 规则 | 位置 | 问题 | 影响 | 建议 |
| --- | --- | --- | --- | --- | --- |
| 高 | `PY003` | L33 | 使用了 eval() | 动态执行字符串代码…… | 改用 `ast.literal_eval`…… |
| 高 | `PY004` | L37 | 通过 shell 执行命令 | `os.system` 存在命令注入风险…… | 改用 `subprocess.run([...])`…… |
| 高 | `PY001` | L44 | 使用了裸 except | 会捕获所有异常…… | 改为捕获具体异常类型…… |
```

完整输出见 [docs/sample_report.md](docs/sample_report.md)，更多命令见 [examples/demo_commands.md](examples/demo_commands.md)。

## 2. 使用说明

### 2.1 一次性任务

```bash
python main.py "审查 examples/buggy_sample.py"          # 代码审查
python main.py "解释 examples/clean_sample.py"          # 代码解释
python main.py "为 examples/buggy_sample.py 生成单元测试" # 生成测试脚手架
python main.py "运行 examples/buggy_sample.py"          # 运行并汇报输出
python main.py "列出目录"                                # 探查项目结构
python main.py "搜索 TODO"                              # 全文搜索
```

### 2.2 交互模式

```bash
python main.py
```

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示帮助 |
| `/tools` | 列出可用工具 |
| `/history` | 查看当前会话的对话记录 |
| `/clear` | 清空上下文（保留 system prompt） |
| `/session <名称>` | 切换 / 新建会话 |
| `/save` | 手动保存会话 |
| `/exit` | 退出 |

### 2.3 常用命令行参数

| 参数 | 说明 |
| --- | --- |
| `--provider {auto,openai,mock}` | `auto`（默认）：有 Key 用在线模型，没有则离线；`mock` 强制离线 |
| `--model` / `--base-url` / `--api-key` | 覆盖模型、接口地址、密钥 |
| `--workspace` | 指定工作区目录，所有路径都被限制在此目录内 |
| `--session` / `--new-session` | 指定或重置会话（多轮记忆保存在 `.agent_sessions/`） |
| `--read-only` | 只读模式，禁用 `write_file` 与 `run_python` |
| `--no-exec` | 仅禁用代码执行 |
| `--max-iterations` | 单轮任务的最大循环次数，默认 6 |
| `--json` | 以 JSON 输出答案与执行轨迹，便于自动化评测 |
| `-q` / `-v` | 安静模式 / 显示模型中间输出 |
| `--show-tools` / `--list-sessions` | 查看工具清单 / 已保存会话 |

## 3. 项目结构

```text
code-assistant-agent/
├── main.py                  # 命令行入口
├── agent/
│   ├── agent.py             # Agent 主循环（输入 → 推理 → 工具 → 输出）
│   ├── llm.py               # OpenAI 兼容客户端 + 离线规则引擎 + 重试
│   ├── tools.py             # 工具注册表与 6 个内置工具
│   ├── analysis.py          # 静态分析引擎（AST + 通用文本检查）
│   ├── memory.py            # 多轮记忆与持久化
│   ├── prompts.py           # system prompt 与输出规范
│   ├── config.py            # 配置（环境变量 / 命令行）
│   └── cli.py               # 交互式与一次性命令行界面
├── examples/                # 演示样例（一个好、一个坏）
├── tests/                   # 52 个单元测试
├── docs/                    # 设计补充、演示脚本、样例报告
├── scripts/package.py       # 打包成「学号姓名.zip」
├── Design.md                # 详细设计文档
└── README.md
```

## 4. 架构与 Agent 循环

```text
用户输入
   │
   ▼
┌─────────────┐   工具描述(schema)   ┌──────────────┐
│  记忆 Memory │ ──────────────────► │  LLM 客户端   │
│  消息列表    │ ◄────────────────── │ 推理 + 决策   │
└─────────────┘   content/tool_calls └──────┬───────┘
   ▲                                        │ 有工具调用
   │ 写回 tool 结果                          ▼
   │                              ┌────────────────────┐
   └──────────────────────────────│ 工具注册表 execute() │
                                  │ read/list/search/   │
                                  │ analyze/run/write   │
                                  └────────────────────┘
                                           │ 无工具调用
                                           ▼
                                        最终答案
```

关键设计点（详见 [Design.md](Design.md)）：

1. **LLM 抽象**：`create_client()` 按配置返回在线客户端或离线规则引擎，两者接口一致（`chat(messages, tools)`），Agent 循环不需要关心具体实现。
2. **工具统一协议**：每个工具都有 JSON Schema 描述、统一返回 `{"ok": bool, ...}`，异常被捕获后转成结构化结果交回模型，循环不会因工具报错而中断。
3. **记忆裁剪**：会话持久化到 JSON，超出上限时按「从 user 消息开始」裁剪，避免产生孤立的 `tool` 消息导致接口报错。
4. **只统计本轮的推理结果**：模型只看到历史对话；离线规则引擎只把「本轮」的工具结果纳入决策，防止上一轮结论串味。

## 5. 内置工具

| 工具 | 功能 | 关键安全约束 |
| --- | --- | --- |
| `read_file` | 读取文本文件，返回带行号内容 | 限制在工作区内；拒绝二进制；支持 `start_line`/`end_line` 与长度截断 |
| `list_dir` | 列出目录（可递归） | 自动跳过 `.git`、`__pycache__`、`node_modules` 等目录 |
| `search_in_files` | 关键字 / 正则搜索 | 跳过二进制与大文件；结果数与文件大小都有上限 |
| `analyze_code` | 静态分析，输出问题清单与评分 | 纯静态、只读，不执行代码 |
| `run_python` | 执行 Python 文件或代码片段 | 超时终止、输出截断、只读模式禁用 |
| `write_file` | 写入文件（如生成测试） | 限制在工作区内；默认拒绝覆盖，必须显式 `overwrite=true` |

## 6. 配置项

优先级：命令行参数 > 环境变量 > 默认值。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CODE_AGENT_API_KEY` | 空 | API Key，未设置时自动进入离线模式；回退读取 `OPENAI_API_KEY` |
| `CODE_AGENT_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容接口地址；回退 `OPENAI_BASE_URL` |
| `CODE_AGENT_MODEL` | `gpt-4o-mini` | 模型名；回退 `OPENAI_MODEL` |
| `CODE_AGENT_PROVIDER` | `auto` | `auto` / `openai` / `mock` |

接入国产模型的示例（通义千问 compatible-mode）：

```powershell
$env:CODE_AGENT_API_KEY = "<你的百炼 API Key>"
$env:CODE_AGENT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:CODE_AGENT_MODEL = "qwen-plus"
python main.py "审查 examples/buggy_sample.py"
```

本地无 Key 服务（如 Ollama / LM Studio）需要显式指定 `--provider openai`：

```bash
python main.py --provider openai --base-url http://localhost:11434/v1 --model qwen2.5-coder "审查 examples/buggy_sample.py"
```

## 7. 测试

```bash
cd code-assistant-agent
python -m unittest discover -s tests -t . -v
```

52 个用例覆盖：静态分析规则与评分、6 个工具的常规与边界行为、路径越界 / 只读模式 / 禁用执行等安全约束、记忆裁剪与持久化、Agent 循环的终止条件与错误处理、命令行端到端冒烟测试。全部用例不依赖网络与 API Key。

## 8. 边界情况与错误处理

| 情况 | 行为 |
| --- | --- |
| 未配置 API Key | 自动降级为离线规则引擎并给出提示，不会崩溃 |
| 网络超时 / 429 / 5xx | 指数退避重试（默认 3 次），仍失败则返回可读的排查建议 |
| 401 / 404 | 提示检查 API Key、接口地址与模型名 |
| 文件不存在 / 是目录 / 是二进制 | 工具返回结构化错误，Agent 直接解释原因，不再无效重试 |
| 路径越界（`../` 或绝对路径） | 直接拒绝，保证只在工作区内操作 |
| 运行代码死循环 | 超时终止进程，并提示检查阻塞点 |
| 单轮任务不收敛 | 达到 `max_iterations` 后返回已执行步骤与收敛建议 |
| 会话文件损坏 | 忽略损坏内容，回退到空会话 |

## 9. 提交与打包

```bash
# 在项目根目录执行，生成 <学号><姓名>.zip
python scripts/package.py --sid 20250001 --name 张三

# 也可以指定输出目录
python scripts/package.py --sid 20250001 --name 张三 --output ../
```

打包会排除 `.git`、`__pycache__`、`.agent_sessions`、`.test_workspaces`、`.env` 等无关文件，产物体积通常小于 200 MB（本项目约几十 KB）。提交前建议把代码推送到 GitHub / Gitee 并附上仓库地址。

## 10. 常见问题

**Q：没有 API Key 可以交作业吗？**
A：可以。默认的 `auto` 模式在没有 Key 时会使用内置离线规则引擎，Agent 循环、工具调用、记忆、错误处理全部真实生效，只是「语言组织」由确定性模板完成。有 Key 时把 `--provider` 换成 `openai` 即可获得大模型的自由推理能力。

**Q：为什么还要保留离线模式？**
A：一是课堂演示与评审时不依赖网络和额度；二是单元测试可以完全确定性地验证 Agent 循环；三是离线模式对每条结论都给出行号与规则编号，可作为评测大模型输出的参照基线。

**Q：如何新增一个工具？**
A：在 `agent/tools.py` 的 `CodeTools` 中实现一个接收 `dict`、返回 `dict` 的方法，然后在 `build_default_registry()` 里 `registry.register(Tool(...))` 注册参数 Schema 即可，Agent 无需改动。
