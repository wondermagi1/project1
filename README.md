# 代码助手 Agent

基于 Python 标准库的代码助手，支持代码审查、解释、生成、测试生成和重构建议。命令行与网页共用同一套 Agent 循环：接收任务，由模型选择工具，将工具结果交回模型，再输出结论。

项目支持兼容 Chat Completions 协议的在线模型，也提供明确标识的离线规则引擎，方便无网络演示和自动化测试。离线模式不代表真实大模型能力。

## 快速启动

需要 Python 3.10 或更高版本。进入项目根目录，无需安装第三方 Python 包。

```bash
python webui.py --provider mock --open
```

浏览器打开 `http://127.0.0.1:8000/`，模型选择“离线”，点击“审查问题样例”。页面会展示工具调用过程、审查报告及下载按钮。上传文本代码文件后可选择审查或解释，同名上传会保留原文件。

命令行体验：

```bash
python main.py --provider mock --new-session "审查 examples/buggy_sample.py"
python main.py --provider mock --mode explain "examples/clean_sample.py"
python main.py --provider mock --mode test "examples/clean_sample.py"
python main.py --provider mock --mode refactor "examples/buggy_sample.py"
python main.py --provider mock --mode generate "写一个列表去重函数"
```

测试和重构方向会创建文件。已有同名产物可能触发防覆盖提示，此时查看已有文件或指定新的工作区。

## 工程工作台

默认首页为项目概览，界面分为文件侧栏、项目概览、代码助手和 Git 改动三个工作区。浅色界面配合深色源码预览，窄屏自动调整布局。

- **文件浏览**：搜索文件名、只读查看带行号的源码，选中文件后将审查或解释任务填入助手。
- **项目扫描**：跨文件汇总问题，显示严重度、平均规则评分与扫描行数。按等级筛选，点击问题定位源码，导出结构化 JSON。
- **改动扫描**：只扫描 Git 已暂存、未暂存及新增的当前源文件，删除文件不参与源码分析。
- **Git 改动**：分开呈现暂存与未暂存差异，新文件可预览内容；“交给助手审查”会准备任务，由你发送。
- **项目测试**：输入 unittest 测试目录，显示实际用例数、失败、跳过和输出。此按钮会执行测试代码，受只读和禁止执行配置约束。
- **会话管理**：新建独立会话，恢复已保存的会话，继续处理同一个任务。

打开其他真实项目：

```bash
python webui.py --workspace "你的项目绝对路径" --provider mock --open
python main.py --workspace "你的项目绝对路径" --provider mock "扫描项目"
python main.py --workspace "你的项目绝对路径" --provider mock "改动扫描"
python main.py --workspace "你的项目绝对路径" --provider mock "审查 Git 改动"
```

Git 功能需要 Git 可执行程序，工作区必须是仓库根目录。离线 Git 审查只提供差异证据；业务语义分析需要在线模型。项目索引最多 500 个文本文件，默认扫描最多 100 个源文件，单文件不超过 300 KB，最多展示 500 条问题；达到上限或跳过文件时会明确标记。

索引跳过隐藏目录、环境文件、依赖目录、构建产物、生成目录与符号链接，不执行完整 `.gitignore` 规则解释；敏感信息也可能存在于普通源码中。源码分析以 Python AST 为主，其余语言只支持通用文本规则。目前测试入口使用 unittest，不冒充支持 pytest、Jest 等其他测试框架。

## 在线模型配置

将 `.env.example` 复制为 `.env`，如果本地已经存在 `.env`，直接编辑现有文件。填写服务商提供的密钥、兼容接口地址和模型名称。

```dotenv
CODE_AGENT_API_KEY=填写自己的密钥
CODE_AGENT_BASE_URL=填写兼容接口的根地址
CODE_AGENT_MODEL=填写服务商支持的模型名称
CODE_AGENT_PROVIDER=auto
```

客户端会在根地址后添加 `/chat/completions`，不要重复填写该后缀。环境变量优先于 `.env`，命令行显式参数优先级最高。配置后执行：

```bash
python main.py --check-api
python webui.py --open
```

`--check-api` 会实际发送一次请求，可能消耗模型额度。网页选择“在线”或“自动”后使用本地服务配置。密钥只由后端读取。提交包排除 `.env` 及其常见变体，保留空白示例。

## 功能与能力边界

| 模式 | 在线模式 | 离线模式 |
| --- | --- | --- |
| review 审查 | 模型结合文件内容和静态分析给出建议 | AST 和文本规则报告、行号、严重度 |
| explain 解释 | 解释逻辑、回答代码问题 | 函数/类结构说明与 docstring 草稿 |
| generate 生成 | 按需求调用写入和验证工具 | 写入带 TODO / NotImplementedError 的骨架 |
| test 测试生成 | 基于行为生成测试并可运行 | 导入与符号检查、待补充的测试骨架 |
| refactor 重构 | 基于分析提出修改并可生成副本 | 有限的文本整理、重构副本和差异 |
| ask 通用问答 | 围绕工作区与用户问题调用工具 | 目录、搜索等预设流程 |

`auto` 自动识别模式。离线测试骨架包含跳过项，测试通过不代表业务逻辑已覆盖。静态评分是规则扣分得到的参考值，不是老师评分，也不是正确性证明。

十个工具：`read_file`、`list_dir`、`search_in_files`、`analyze_code`、`write_file`、`run_python`、`run_tests`、`diff_files`、`project_scan`、`git_diff`。

## 报告与会话

```bash
python main.py --provider mock --json "审查 examples/clean_sample.py"
python main.py --provider mock --export generated/review.md "审查 examples/buggy_sample.py"
python main.py --provider mock --export generated/review.json "审查 examples/buggy_sample.py"
python main.py --provider mock --session homework
```

`--json` 只向标准输出写入 JSON。`--export` 保存任务、实际模型来源、结果、结束状态及执行轨迹，只支持一次性任务，拒绝覆盖已有报告。网页结果下方可以下载 Markdown 和 JSON。

交互命令：`/mode review` 切换模式，`/session demo` 切换会话并保留当前模式，`/history` 查看记录，`/clear` 清空当前上下文，`/save` 保存，`/exit` 退出。会话位于 `.agent_sessions/`。网页提供历史会话下拉框，选中后恢复已保存的用户消息与最终回答；新建会话使用独立的名称。历史恢复不包含之前页面的下载按钮和工具气泡，完整执行证据请在任务完成后导出。

## 执行范围

```bash
python main.py --provider mock --read-only "审查 examples/buggy_sample.py"
python webui.py --provider mock --read-only
python webui.py --no-exec
```

`--read-only` 禁止 Agent 写文件、执行 Python 和测试，网页也禁止上传；会话与显式报告导出仍可保存。`--no-exec` 同时禁止 Python 执行和测试运行，仍允许文件写入。

文件工具检查工作区路径，但执行子进程不是操作系统沙箱，Python 代码仍具备当前用户权限。仅对可信代码启用执行。本地 Web 服务用于个人演示，不带账户鉴权，不应直接用于公网服务。

## 验证

```bash
python -m unittest discover -s tests -v
```

测试使用离线或伪造模型响应，不需要真实密钥。网页测试在 `127.0.0.1` 上创建临时 HTTP 服务。可选的前端回归检查需要 Node.js，无需 npm 安装：

```bash
node tests/test_webui.cjs
```

测试覆盖 Agent 循环、模式选择、工具边界、会话裁剪、模型错误与重试、HTTP 和流式响应、导出及 Markdown 特殊输入。以本次运行结果为准，示例报告不替代实际验证。

## 作业要求对应

| 作业要求 / 评分项 | 项目实现与证据 |
| --- | --- |
| 输入、推理、工具调用、输出循环 | `agent/agent.py`，网页实时步骤及 JSON 轨迹 |
| 至少一种工具 | 十个工具统一注册，见 `agent/tools.py` |
| CLI 或简单 Web 交互 | `main.py` 和 `webui.py` 两个入口 |
| LLM 调用与 Prompt 设计 | `agent/llm.py`、`agent/prompts.py`、`agent/modes.py` |
| 推荐的记忆、错误处理、重试 | JSON 会话、按回合裁剪、结构化工具错误、指数退避 |
| 功能完整性 40% | 五个方向，共用工具，失败和边界场景有回归测试 |
| Agent 架构 30% | 模型适配器、工具注册表、记忆、交互层分离 |
| 代码质量 20% | 标准库实现、类型标注、自动化测试 |
| 文档 10% | 本文、`Design.md`、演示脚本、验收记录 |

## 提交

```bash
python scripts/package.py --sid 你的学号 --name 你的姓名 --output dist
```

生成 `dist/学号姓名.zip`。打包前检查核心文档存在，排除会话、上传、生成产物、缓存、密钥配置与旧压缩包，并检查 200 MB 大小限制。

源代码仓库：[wondermagi1/project1](https://github.com/wondermagi1/project1)。提交前需要填写自己的**学号、姓名**。若平台要求 RAR 格式，可将核对后的同名文件夹用压缩软件转为 RAR。原始 PPT 标注提交截止为 **2026-10-07 24:00**，延期或补交安排以课程通知为准。

建议先阅读 [设计文档](Design.md)，再按 [一分钟演示脚本](docs/demo_script.md) 录制。
