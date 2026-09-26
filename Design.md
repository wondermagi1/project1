# Design：代码助手 Agent 设计文档

## 1. 需求解读

作业要求搭建一个代码助手 Agent，掌握 LLM 调用、Prompt 设计、工具集成等基础能力。逐条对应如下：

| 作业要求 | 本项目的实现 |
| --- | --- |
| 基本的 Agent 循环：输入 → 推理 → 工具调用 → 输出 | `agent/agent.py::CodeAgent.run()`，多轮循环，最多 `max_iterations` 次 |
| 支持至少一种工具 | 内置 6 个工具：`read_file` / `list_dir` / `search_in_files` / `analyze_code` / `run_python` / `write_file` |
| 可通过命令行或简单 Web 界面交互 | 命令行一次性任务 + 交互式 REPL（含 `/tools`、`/history` 等命令） |
| 使用主流框架或 LLM 原生 API | 直接调用 OpenAI 兼容的 `/chat/completions`（function calling），不依赖框架，标准库 `urllib` 实现 |
| 支持上下文记忆 | `agent/memory.py`，多轮消息 + JSON 持久化 + 自动裁剪 |
| 错误处理与重试 | HTTP 重试与指数退避、工具异常结构化返回、循环上限保护、路径与超时约束 |
| 技术栈自由选择 | Python 3.10+，仅标准库 |

选定的作业方向是 **代码审查 Agent**（分析代码质量、发现潜在 Bug、给出改进建议），并在同一套架构上顺带支持代码解释与测试生成，以验证工具的复用性。

## 2. 总体架构

```text
                      ┌──────────────────────────────┐
                      │           cli.py             │
                      │ 参数解析 / REPL / 轨迹打印     │
                      └──────────────┬───────────────┘
                                     │ 构造
              ┌──────────────────────┼───────────────────────┐
              ▼                      ▼                       ▼
      ┌──────────────┐      ┌──────────────┐        ┌──────────────┐
      │  config.py   │      │  agent.py    │───────►│  memory.py   │
      │ 配置与校验    │      │ Agent 主循环  │◄───────│ 记忆与持久化  │
      └──────────────┘      └───┬──────┬───┘        └──────────────┘
                                │      │
                    推理请求/响应 │      │ 工具调用
                                ▼      ▼
                        ┌──────────┐  ┌──────────────┐
                        │  llm.py  │  │  tools.py    │
                        │ 在线/离线 │  │ 工具注册表    │
                        └──────────┘  └──────┬───────┘
                                             ▼
                                     ┌──────────────┐
                                     │ analysis.py  │
                                     │ 静态分析引擎  │
                                     └──────────────┘
```

分层原则：**Agent 主循环只依赖抽象接口**（`client.chat()` 与 `registry.execute()`），因此 LLM 提供方、工具集合都可以独立替换或扩展。

## 3. 模块设计

### 3.1 `config.py`：配置

* `AgentConfig` 是唯一配置载体，`from_env()` 负责「环境变量 + 命令行覆盖」，`__post_init__` 做范围收敛（迭代次数 1–20、重试 1–8 次等），避免非法参数直接崩溃。
* `active_provider` 实现 `auto` 语义：有 Key 走在线模型，否则走离线规则引擎。
* 会话目录默认落在工作区内的 `.agent_sessions/`，便于随项目清理与忽略。

### 3.2 `llm.py`：LLM 客户端

两个实现共享同一个接口：

```python
class LLMClient(Protocol):
    def chat(self, messages, tools) -> LLMResponse: ...
```

* `OpenAICompatibleClient`
  * 请求 `POST {base_url}/chat/completions`，携带 `tools` 与 `tool_choice="auto"`；
  * 内部消息 → API 消息的转换集中在 `_to_api_message()`：`tool` 角色带 `tool_call_id`，带工具调用的 assistant 消息 `content` 置为 `null`；
  * 重试策略：429 / 408 / 425 / 5xx 以及网络异常可重试，间隔 `retry_backoff ** (attempt-1)`，上限 8 秒；4xx（除限流类）直接抛出并附带接口返回的错误体。
* `MockLLM`（离线规则引擎）
  * 确定性策略：`read_file → analyze_code → 生成结论`，或 `run_python → 汇总输出`，或 `list_dir / search_in_files → 汇总`；
  * 意图识别基于关键词（审查 / 解释 / 测试 / 运行 / 列出 / 搜索），文件路径用正则提取；
  * **只读取本轮**（最后一条 user 消息之后）的工具结果，避免多轮会话中复用上一轮结论；
  * 只做「组织与呈现」，所有事实（行号、严重度、建议）来自静态分析结果，不会编造。

### 3.3 `tools.py`：工具层

```python
@dataclass
class Tool:
    name: str
    description: str
    parameters: dict      # JSON Schema
    func: Callable[[dict], dict]
    dangerous: bool = False
```

* `ToolRegistry.execute()` 是唯一的调用入口，负责：未知工具、只读模式、`ToolError`、`FileNotFoundError`、`TimeoutExpired`、JSON 解析失败以及兜底 `Exception` 的统一处理，**保证不向 Agent 循环抛异常**。
* 每次调用记录耗时与成功状态（`registry.calls`），便于观测与后续统计。
* `CodeTools._resolve()` 统一做路径归一化与越界检查：所有相对路径基于 `workspace` 解析，解析后必须仍位于工作区内，否则拒绝。

### 3.4 `analysis.py`：静态分析引擎

用标准库 `ast` 建立语法树，产出「事实」而非「感觉」：

* 指标：总行数、代码行、注释行、函数数、类数、导入数、最长函数长度、平均函数长度。
* 符号表：类（方法列表、基类、是否有 docstring）、函数（起止行、参数、是否方法、是否有 docstring、装饰器）。
* 规则（Python 专用）：

| 规则 | 严重度 | 检查内容 |
| --- | --- | --- |
| `PY001` | 高 | 裸 `except:` |
| `PY002` | 中 | `except` 分支只有 `pass`（静默吞异常） |
| `PY003` | 高 | 使用 `eval()` / `exec()` |
| `PY004` | 高 | `os.system` / `os.popen` / `subprocess(..., shell=True)` |
| `PY005` | 低 | `subprocess` 调用未设置超时 |
| `PY006` | 中 | 参数默认值为可变对象（`[]`/`{}`/`set()`） |
| `PY007` | 低 | 用 `==` / `!=` 与 `None` 比较 |
| `PY008` | 中 / 低 | 函数过长（> 60 行 / > 30 行） |
| `PY009` | 低 | 公开函数或类缺少 docstring |
| `PY010` | 低 | 函数参数过多（> 5 个） |
| `PY011` | 中 | 通配符导入 `from x import *` |
| `PY012` | 低 | 导入但未使用的符号 |
| `PY013` | 中 | `open()` 未使用 `with` |

* 通用规则（任意语言）：`GEN001` 单行超长、`GEN002` TODO/FIXME、`GEN003` 混用 Tab、`GEN004` 文件结尾缺少换行。
* 语法错误不崩溃：捕获 `SyntaxError`，产出 `SYN001`（高）并给出出错行号。
* 评分：`100 - Σ(高 25 / 中 10 / 低 4)`，下限 0，再映射到「优秀 / 良好 / 一般 / 较差」四档。

### 3.5 `memory.py`：上下文记忆

* 消息列表包含 `system` / `user` / `assistant` / `tool` 四种角色，与 OpenAI 兼容接口一致。
* `save()` 采用「先写 `.tmp` 再 `replace`」的原子写入，避免进程中断导致会话文件损坏；读取失败时静默回退到空会话。
* 裁剪策略：保留全部 `system` 消息，保留最近 `max_messages` 条，并**从一条 `user` 消息开始**。这样可以避免出现「孤立的 `tool` 结果」——这类结构在多数兼容接口上会直接报 400。

### 3.6 `agent.py`：主循环

```text
run(user_input):
    memory.add(user)
    for iteration in 1..max_iterations:
        response = client.chat(memory.messages(), registry.specs())
        if response 没有工具调用:
            写入 assistant 消息 → 返回 final
        写入 assistant(tool_calls)
        for call in response.tool_calls:
            result = registry.execute(call.name, call.arguments)
            写入 tool 消息（JSON 字符串）
    返回 max_iterations（附已执行步骤与收敛建议）
```

三种终止状态：`final`（正常完成）、`max_iterations`（保护）、`error`（LLM 调用失败，附排查建议）。

### 3.7 `cli.py`：交互界面

* 一次性任务：`python main.py "审查 examples/buggy_sample.py"`，`--json` 可输出结构化轨迹，便于自动化评测。
* 交互模式：REPL + `/help`、`/tools`、`/history`、`/clear`、`/session`、`/save`、`/exit`。
* 执行轨迹实时打印：`[ok] read_file: examples/buggy_sample.py → 读取 1-88 行 / 共 88 行`，让 Agent 的决策过程可验证。

## 4. Prompt 设计

`prompts.py` 中的 system prompt 明确四件事：

1. **角色与能力**：代码审查 / 解释 / 测试生成，可调用工具。
2. **工作流程**：先定位文件 → 读取 → 静态分析 → 必要时运行验证 → 再下结论；并明确要求「所有结论基于工具返回的真实内容，禁止编造行号」。
3. **输出规范**：统一为 Markdown，审查结果包含评分、问题表格（严重度 / 位置 / 影响 / 建议）、优先修复顺序。
4. **安全边界**：只在工作区内操作，写文件前说明，执行代码需用户明确要求，工具报错时解释原因而非重复无效调用。

「禁止编造行号」是刻意设计的约束：把事实性工作交给 `analyze_code`，模型负责归纳与表达，从根上降低幻觉带来的评审风险。

## 5. 错误处理与鲁棒性

| 层次 | 策略 |
| --- | --- |
| 网络层 | 可重试错误指数退避；不可重试错误立即失败并附带响应体 |
| 协议层 | 兼容 `content` 为字符串或分片数组；`tool_calls.arguments` 允许字符串或对象；缺少 `choices` 时给出明确错误 |
| 工具层 | 统一返回 `{"ok": bool, ...}`，异常不向外抛出；耗时被记录 |
| 安全层 | 路径越界拒绝；写文件需显式覆盖开关；只读模式禁用危险工具；执行超时终止 |
| 循环层 | `max_iterations` 上限、空输入、LLM 失败三种兜底路径 |
| 存储层 | 原子写入 + 损坏文件回退 |

## 6. 测试策略

共 52 个用例（`python -m unittest discover -s tests -t .`），全部离线、无网络依赖：

| 测试文件 | 覆盖重点 |
| --- | --- |
| `test_analysis.py` | 13 条规则命中、行号与严重度、干净代码满分、语法错误降级、符号表与指标 |
| `test_tools.py` | 读文件（分页 / 缺失 / 目录 / 二进制）、路径越界、列表跳过缓存目录、搜索与非法正则、写文件覆盖保护、运行成功 / 失败 / 超时、只读模式与 `--no-exec`、Schema 合法性 |
| `test_memory.py` | 会话名净化、轮次统计、清空保留 system、裁剪不产生孤立 tool 消息、保存 / 加载 / 损坏回退 |
| `test_agent.py` | 离线多步循环、三种意图分支、缺文件不崩溃、禁用执行、最大迭代保护、LLM 错误提示、跨轮记忆、本轮工具结果隔离、事件回调 |
| `test_cli.py` | `--show-tools`、一次性审查、`--json` 轨迹、`--list-sessions` |

## 7. 已知限制与改进方向

1. **离线规则引擎不做语义理解**：它基于关键词与静态事实，无法像大模型那样推理业务意图；定位是「无 Key 时的演示基线与确定性测试替身」。
2. **静态分析以 Python 为主**：其他语言目前只有通用文本规则，可接入 `eslint`、`spotbugs`、`clippy` 等外部 linter，经 `run_python`/新增工具统一成同一份 JSON 结构。
3. **无并发与流式输出**：当前为单轮同步调用；如需流式，可把 `chat()` 改为生成器并在 CLI 增量打印。
4. **仅命令行界面**：可基于同一 `CodeAgent` 暴露 FastAPI/Flask 路由，前端只需流式转发 `AgentStep` 事件。
5. **未做 AST 数据流分析**：如未使用变量、不可达代码、类型错误等，可在同一 `Finding` 结构下继续扩展规则。
