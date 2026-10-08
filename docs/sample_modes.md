# 五个方向的运行样例（离线模式）

下面是 `--provider mock`（不联网、不消耗 API 额度）下四个模式的真实输出节选。
代码审查方向的完整报告见 [sample_report.md](sample_report.md)，接入真实模型的对比见 [sample_report_online.md](sample_report_online.md)。

每个模式都会真实调用工具：解释模式生成注释 diff，生成模式写文件并运行，
测试模式写测试文件并执行，重构模式输出坏味道清单 + diff + 重构副本。

---

## 代码解释模式

```bash
python main.py --provider mock --mode explain "解释 examples/buggy_sample.py"
```

```text
provider=mock  model=deepseek-chat  mode=explain  workspace=C:\Users\GXR\Documents\Codex\2026-09-26\new-chat\work\demo-ws
提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。
      想接入真实大模型：编辑项目里的 .env 填入 API Key，再运行 python main.py --check-api 自检。
  [ok  ] read_file: examples/buggy_sample.py → 读取 1-88 行 / 共 88 行
  [ok  ] analyze_code: examples/buggy_sample.py → 评分 0，问题 19 项（高 3 / 中 3 / 低 13）
  [ok  ] diff_files: {"ok": true, "left": "examples/buggy_sample.py", "right": "<建议版本>", "changed": true, "added_lines": 6, "removed_lines": 0, "diff": "--- a/examples/buggy_sample.py\n+++ b/<建议版本>\n@@ -10,6 +10,7 @@\n \n

# 代码说明：`examples/buggy_sample.py`

文件共 88 行，包含 0 个类、7 个函数。

## 结构概览

- 该文件没有定义类。
- 顶层函数：
  - `add_item(inventory, name, price, quantity, category, supplier, note)`（L12-L22，11 行）
  - `load_inventory(path)`（L25-L29，5 行）
  - `parse_expression(expression)`（L32-L33，2 行）
  - `backup_inventory(path)`（L36-L37，2 行）
  - `run_report(mode)`（L40-L45，6 行）
  - `summarize(inventory, currency, include_zero, sort_key, reverse, limit, verbose)`（L48-L83，36 行）
  - `format_report(summary, width, title, footer)`（L87-L88，2 行）

## 阅读建议

1. 先从 `add_item` 读起，它是这个文件的主要入口。
2. 关注函数之间的数据流向：谁负责读取输入、谁负责处理、谁负责输出。
3. 注意异常分支与边界条件，它们通常是最容易出问题的部分。

## 需要注意的风险点

- L33 **使用了 eval()**：动态执行字符串代码，输入不可信时会直接造成任意代码执行。
- L37 **通过 shell 执行命令**：`os.system` 会把字符串交给 shell 解释，存在命令注入风险且无法拿到返回码。
- L44 **使用了裸 except**：会捕获所有异常（包括 KeyboardInterrupt、SystemExit），真实错误容易被掩盖。

## 建议补充的注释（diff）

```diff
--- a/examples/buggy_sample.py
+++ b/<建议版本>
@@ -10,6 +10,7 @@


 def add_item(inventory={}, name=None, price=0, quantity=0, category=None, supplier=None, note=None):
+    """add_item：TODO 说明职责（参数：inventory, name, price, quantity, category, supplier, note；返回值：TODO）。"""
     if inventory == None:
         inventory = {}
     inventory[name] = {
@@ -23,6 +24,7 @@


 def load_inventory(path):
+    """load_inventory：TODO 说明职责（参数：path；返回值：TODO）。"""
     handle = open(path, "r")
     data = json.load(handle)
     handle.close()
@@ -30,14 +32,17 @@


 def parse_expression(expression):
+    """parse_expression：TODO 说明职责（参数：expression；返回值：TODO）。"""
     return eval(expression)


 def backup_inventory(path):
+    """backup_inventory：TODO 说明职责（参数：path；返回值：TODO）。"""
     os.system("cp " + path + " " + path + ".bak")


 def run_report(mode):
+    """run_report：TODO 说明职责（参数：mode；返回值：TODO）。"""
     try:
         result = subprocess.run(["python", "report.py", mode], capture_output=True)
         return result.stdout
@@ -85,4 +90,5 @@

 # TODO: 把上面的函数按职责拆分，并补充单元测试
 def format_report(summary, width=80, title="Inventory repo
...（输出较长，此处截断）
```

## 代码生成模式

```bash
python main.py --provider mock --mode generate "写一个 quicksort 函数"
```

```text
provider=mock  model=deepseek-chat  mode=generate  workspace=C:\Users\GXR\Documents\Codex\2026-09-26\new-chat\work\demo-ws
提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。
      想接入真实大模型：编辑项目里的 .env 填入 API Key，再运行 python main.py --check-api 自检。
  [fail] write_file: generated/quicksort.py → 失败：文件已存在：generated/quicksort.py；如需覆盖，请显式传入 overwrite=true

工具 `write_file` 执行失败：文件已存在：generated/quicksort.py；如需覆盖，请显式传入 overwrite=true

请调整参数后重试。
```

## 测试生成模式

```bash
python main.py --provider mock --mode test "为 examples/buggy_sample.py 生成单元测试"
```

```text
provider=mock  model=deepseek-chat  mode=test  workspace=C:\Users\GXR\Documents\Codex\2026-09-26\new-chat\work\demo-ws
提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。
      想接入真实大模型：编辑项目里的 .env 填入 API Key，再运行 python main.py --check-api 自检。
  [ok  ] read_file: examples/buggy_sample.py → 读取 1-88 行 / 共 88 行
  [ok  ] analyze_code: examples/buggy_sample.py → 评分 0，问题 19 项（高 3 / 中 3 / 低 13）
  [fail] write_file: examples/test_buggy_sample.py → 失败：文件已存在：examples/test_buggy_sample.py；如需覆盖，请显式传入 overwrite=true

工具 `write_file` 执行失败：文件已存在：examples/test_buggy_sample.py；如需覆盖，请显式传入 overwrite=true

请调整参数后重试。
```

## 重构建议模式

```bash
python main.py --provider mock --mode refactor "重构 examples/buggy_sample.py"
```

```text
provider=mock  model=deepseek-chat  mode=refactor  workspace=C:\Users\GXR\Documents\Codex\2026-09-26\new-chat\work\demo-ws
提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。
      想接入真实大模型：编辑项目里的 .env 填入 API Key，再运行 python main.py --check-api 自检。
  [ok  ] read_file: examples/buggy_sample.py → 读取 1-88 行 / 共 88 行
  [ok  ] analyze_code: examples/buggy_sample.py → 评分 0，问题 19 项（高 3 / 中 3 / 低 13）
  [fail] write_file: examples/buggy_sample_refactored.py → 失败：文件已存在：examples/buggy_sample_refactored.py；如需覆盖，请显式传入 overwrite=true

工具 `write_file` 执行失败：文件已存在：examples/buggy_sample_refactored.py；如需覆盖，请显式传入 overwrite=true

请调整参数后重试。
```
