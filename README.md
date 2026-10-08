# 代码助手 Agent

面向本地软件项目的代码助手与工程工作台。支持代码审查、代码解释、代码生成、测试生成和重构建议，提供项目质量扫描、Git 改动查看、源码预览、会话管理与执行报告导出。

命令行与 Web 界面共用一套 Agent 循环：接收任务、调用模型、执行工具、将结果交回模型，最后输出回答。项目使用 Python 标准库实现，可接入支持工具调用的 Chat Completions 兼容接口，也可使用离线规则引擎。

## 环境要求

| 组件 | 要求 | 用途 |
| --- | --- | --- |
| Python | 3.10 或更高版本 | 运行应用与 Python 测试 |
| 浏览器 | 支持 fetch 流式读取和 dialog 元素 | 使用 Web 工作台 |
| Git | 可在命令行执行 | 克隆仓库、查看差异、扫描改动文件 |
| Node.js | 可选 | 运行前端脚本回归检查 |
| 模型服务 | 可选，支持 Chat Completions 和工具调用 | 使用在线代码分析与生成功能 |

Python 运行时不需要第三方依赖。`requirements.txt` 记录运行环境说明，无需执行依赖安装。离线模式不需要 API 密钥。

## 快速开始

### 1. 获取源码

```bash
git clone https://github.com/wondermagi1/project1.git
cd project1
```

以下命令均在项目根目录执行。已有源码时直接进入对应目录。

### 2. 启动工作台

```bash
python webui.py --provider mock --open
```

默认地址为 `http://127.0.0.1:8000/`。首页点击“扫描项目”查看质量问题；进入“代码助手”页，选择“离线规则引擎”，点击“审查问题样例”体验完整工具流程。按 `Ctrl+C` 停止服务。

端口已被占用时指定其他端口：

```bash
python webui.py --provider mock --port 9000 --open
```

### 3. 使用命令行

```bash
python main.py --provider mock --new-session "审查 examples/buggy_sample.py"
```

终端会显示工具调用和最终结果。省略任务描述进入交互模式：

```bash
python main.py --provider mock
```

离线模式用于本地试用和流程验证。代码生成主要产出骨架，测试生成包含待补充或跳过的用例；完整业务分析需要配置在线模型并检查实际产出。

## 主要功能

| 功能 | 使用方式 |
| --- | --- |
| 文件浏览 | 在侧栏搜索文件、预览源码，并准备审查或解释任务 |
| 项目扫描 | 汇总跨文件问题、行号与严重度，筛选问题并导出 JSON |
| 改动扫描 | 分析 Git 已暂存、未暂存和新增源文件的当前内容 |
| Git 改动 | 分别查看暂存与未暂存差异，预览新增文件 |
| 项目测试 | 运行指定目录的 unittest 测试，查看失败、跳过及输出 |
| 代码助手 | 选择任务模式，查看工具执行过程与最终回答 |
| 会话管理 | 新建会话、恢复历史对话、继续处理任务 |
| 报告导出 | 下载 Markdown 结果报告和 JSON 执行轨迹 |

打开其他项目：

```bash
python webui.py --workspace "项目绝对路径" --provider mock --open
python main.py --workspace "项目绝对路径" --provider mock "扫描项目"
python main.py --workspace "项目绝对路径" --provider mock "改动扫描"
python main.py --workspace "项目绝对路径" --provider mock "审查 Git 改动"
```

Git 功能要求工作区是仓库根目录。静态扫描分析当前文件内容，不能替代基于差异的业务审查。

## 在线模型配置

首次配置时，将 `.env.example` 复制为 `.env`。若已存在 `.env`，直接编辑该文件。

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

macOS / Linux：

```bash
cp .env.example .env
```

配置项：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CODE_AGENT_API_KEY` | 空 | 模型服务密钥，也支持 `OPENAI_API_KEY` |
| `CODE_AGENT_BASE_URL` | `https://api.openai.com/v1` | 兼容接口根地址，也支持 `OPENAI_BASE_URL` |
| `CODE_AGENT_MODEL` | `gpt-4o-mini` | 配置为服务商实际支持的模型名，也支持 `OPENAI_MODEL` |
| `CODE_AGENT_PROVIDER` | `auto` | `auto` 自动选择、`openai` 在线适配器、`mock` 离线规则引擎 |

客户端会在根地址后添加 `/chat/completions`，不要重复填写该后缀。支持覆盖的命令行参数优先于非空环境变量，环境变量优先于 `.env` 和程序默认值。`auto` 在检测到密钥时使用在线模型，否则使用离线规则引擎。

验证配置并启动在线工作台：

```bash
python main.py --check-api
python webui.py --provider openai --open
```

`--check-api` 会实际调用模型服务，可能产生费用。网页中的“自动”继承服务启动配置；明确选择“在线模型”或“离线规则引擎”可切换本次任务来源。密钥由后端读取，不随源码归档分发。

## 任务模式与命令

| 模式 | 任务 | 离线模式的能力 |
| --- | --- | --- |
| `auto` | 自动识别任务 | 识别预设关键词与任务流程 |
| `review` | 代码审查 | AST / 文本规则分析，输出行号和建议 |
| `explain` | 代码解释 | 提取类、函数与导入，生成结构说明 |
| `generate` | 代码生成 | 写入带 TODO / NotImplementedError 的骨架 |
| `test` | 测试生成 | 生成导入、符号检查与待补充的测试骨架 |
| `refactor` | 重构建议 | 有限文本整理、生成副本并展示差异 |
| `ask` | 工作区问答 | 目录、搜索等预设流程 |

在线模式由模型结合文件和工具结果处理任务。写入是否成功、测试是否通过，以实际工具结果为准。

```bash
python main.py --provider mock --mode explain "examples/clean_sample.py"
python main.py --provider mock --mode test "examples/clean_sample.py"
python main.py --provider mock --mode refactor "examples/buggy_sample.py"
python main.py --provider mock --mode generate "写一个列表去重函数"
python main.py --show-tools
python main.py --help
python webui.py --help
```

测试与重构任务会创建文件。在线模型可请求覆盖；离线测试和重构会更新相应生成文件。处理已有项目时，建议先保存当前修改。

## 会话与报告

```bash
python main.py --provider mock --session project-review
python main.py --provider mock --list-sessions
python main.py --provider mock --json "审查 examples/clean_sample.py"
python main.py --provider mock --export generated/review.md "审查 examples/buggy_sample.py"
python main.py --provider mock --export generated/review.json "审查 examples/buggy_sample.py"
```

`--json` 将结果以 JSON 写入标准输出。`--export` 仅用于一次性任务，记录任务、实际模型来源、结束状态、回答与步骤，拒绝覆盖已有报告。

| 交互命令 | 说明 |
| --- | --- |
| `/mode review` | 切换任务模式 |
| `/session demo` | 新建或切换会话，保留当前模式 |
| `/history` | 查看记录 |
| `/clear` | 清空当前会话上下文 |
| `/save` | 保存会话 |
| `/exit` | 退出 |

会话保存在工作区的 `.agent_sessions/`。网页历史会话恢复用户消息与最终回答，不恢复之前的工具气泡和下载按钮；需要完整执行记录时请导出报告。

## 目录结构

```text
project1/
├── agent/              Agent 循环、模型适配、工具、记忆及 Web 服务
│   └── static/         工作台样式与浏览器脚本
├── docs/               设计补充、工作流与验证记录
├── examples/           示例源码与报告相关样例
├── scripts/            源码归档工具
├── tests/              自动化测试
├── main.py             命令行入口
├── webui.py            Web 工作台入口
├── .env.example        模型配置模板
├── Design.md           架构设计文档
└── requirements.txt    Python 运行环境说明
```

`.agent_sessions/`、`uploads/`、`generated/`、`dist/` 为运行或构建目录，Git 忽略这些内容。

## 测试与源码打包

Python 测试使用离线或伪造模型响应，不需要真实密钥。Web 测试创建本机临时 HTTP 服务。

```bash
python -m unittest discover -s tests -v
```

安装 Node.js 后，可运行不依赖 npm 包的前端回归检查：

```bash
node tests/test_webui.cjs
```

源码打包：

```bash
python scripts/package.py
python scripts/package.py --package-name code-assistant-agent-1.0.0 --output dist
```

默认生成 `dist/code-assistant-agent.zip`。归档包含源码与文档，排除会话、上传、生成产物、缓存、密钥配置和旧压缩包。`--package-name` 指定名称，`--output` 指定输出目录，`--force` 允许覆盖已有归档。

## 使用范围与限制

```bash
python main.py --provider mock --read-only "审查 examples/buggy_sample.py"
python webui.py --provider mock --read-only
python webui.py --no-exec
```

`--read-only` 禁止 Agent 写文件、上传、执行 Python 和测试，但允许会话持久化及显式报告导出。`--no-exec` 禁止执行 Python 和测试，仍允许写文件。

- Python 源码使用 AST 分析，其余语言使用通用文本规则。规则可能漏报或误报，评分仅供辅助定位。
- 项目索引最多 500 个文本文件，默认扫描 100 个源文件，单文件限 300 KB，最多返回 500 条问题。截断和跳过会明确标记。
- 索引排除隐藏目录、环境文件、常见依赖与构建目录及符号链接，尚未完整实现 `.gitignore` 规则解释。
- 测试入口支持 unittest，尚未集成 pytest 或 Jest。
- 执行子进程使用当前用户权限，没有容器隔离。文件工具的工作区限制不等于代码执行沙箱。
- Web 服务默认仅监听本机，没有账户鉴权，适用于本地可信项目。
- 同一 Web 服务内的同名会话串行处理，尚未提供跨进程会话锁或 token 摘要。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 默认端口被占用 | 使用 `--port 9000`，访问对应端口 |
| 仍在使用离线模式 | 检查模型密钥及 provider；Web 启动时指定 `--provider openai` |
| API 返回 401 / 403 | 检查密钥和模型访问权限 |
| API 返回 404 | 检查根地址、模型名称，以及服务是否兼容 `/chat/completions` |
| 网络超时或限流 | 检查连接与服务状态，可切换 `--provider mock` 验证本地流程 |
| Git 状态读取失败 | 确认 Git 可执行、`--workspace` 指向仓库根目录 |
| 报告已存在，无法导出 | 更换报告路径；导出默认保护已有文件 |
| 文件无法预览或被跳过 | 检查 UTF-8 编码、文件大小和允许访问的路径 |
| 测试显示跳过 | 检查测试是否为待补充骨架，跳过项不等于业务验证成功 |

## 文档与仓库

- [架构设计](Design.md)
- [项目工作流](docs/workflow.md)
- [验证记录](docs/validation.md)
- [源码仓库](https://github.com/wondermagi1/project1)

当前仓库尚未提供 LICENSE 文件。
