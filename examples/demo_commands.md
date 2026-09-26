# 演示命令

在没有 API Key 的情况下，用离线模式即可完整演示 Agent 的「输入 → 推理 → 工具调用 → 输出」循环。

| 场景 | 命令 |
| --- | --- |
| 代码审查（多问题样例） | `python main.py --provider mock "审查 examples/buggy_sample.py"` |
| 代码审查（整洁样例） | `python main.py --provider mock "审查 examples/clean_sample.py"` |
| 代码解释 | `python main.py --provider mock "解释 examples/buggy_sample.py"` |
| 生成单元测试 | `python main.py --provider mock "为 examples/buggy_sample.py 生成单元测试"` |
| 运行验证 | `python main.py --provider mock "运行 examples/clean_sample.py"` |
| 结构探查 | `python main.py --provider mock "列出目录"` |
| 全文搜索 | `python main.py --provider mock "搜索 TODO"` |
| 查看执行轨迹（JSON） | `python main.py --provider mock --json "审查 examples/buggy_sample.py"` |

接入真实模型（以 OpenAI 为例）：

```bash
# macOS / Linux
export CODE_AGENT_API_KEY=sk-xxxx
# Windows PowerShell
$env:CODE_AGENT_API_KEY = "sk-xxxx"

python main.py --provider openai --model gpt-4o-mini "审查 examples/buggy_sample.py"
```

接入 OpenAI 兼容的国产模型（示例：通义千问 compatible-mode）：

```powershell
$env:CODE_AGENT_API_KEY = "<你的百炼 API Key>"
$env:CODE_AGENT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:CODE_AGENT_MODEL = "qwen-plus"
python main.py "审查 examples/buggy_sample.py"
```
