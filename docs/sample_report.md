# 样例运行结果

以下输出由 `--provider mock`（离线规则引擎）在真实代码上运行生成，可复现：

```bash
python main.py --provider mock "审查 examples/buggy_sample.py"
```

## 1. 执行轨迹与审查报告

```text
provider=mock  model=gpt-4o-mini  workspace=...\code-assistant-agent
提示：当前为离线模式（未配置 API Key），使用内置规则引擎演示完整 Agent 循环。
  [ok  ] read_file: examples/buggy_sample.py → 读取 1-88 行 / 共 88 行
  [ok  ] analyze_code: examples/buggy_sample.py → 评分 0，问题 19 项（高 3 / 中 3 / 低 13）

# 代码审查报告：`examples/buggy_sample.py`

**综合评分：0/100（较差，存在明显的正确性或安全风险）**

- 规模：88 行（代码 72 行，注释 1 行）
- 结构：7 个函数 / 0 个类 / 4 处导入
- 最长函数：36 行

## 问题清单（共 19 项：高 3 / 中 3 / 低 13）

| 严重度 | 规则 | 位置 | 问题 | 影响 | 建议 |
| --- | --- | --- | --- | --- | --- |
| 高 | `PY003` | L33 | 使用了 eval() | 动态执行字符串代码，输入不可信时会直接造成任意代码执行。 | 改用 `ast.literal_eval` 解析字面量，或改为显式的映射表 / 分发函数。 |
| 高 | `PY004` | L37 | 通过 shell 执行命令 | `os.system` 会把字符串交给 shell 解释，存在命令注入风险且无法拿到返回码。 | 改用 `subprocess.run([...], check=True, timeout=...)` 以列表形式传参。 |
| 高 | `PY001` | L44 | 使用了裸 except | 会捕获所有异常（包括 KeyboardInterrupt、SystemExit），真实错误容易被掩盖。 | 改为捕获具体异常类型，例如 `except ValueError as exc:`；确实需要兜底时用 `except Exception`。 |
| 中 | `PY006` | L12 | 参数默认值使用了可变对象 | 默认值在函数定义时创建并被所有调用共享，多次调用会互相污染数据。 | 默认值改为 `None`，函数内部再 `if arg is None: arg = []`。 |
| 中 | `PY013` | L26 | open() 未使用 with 语句 | 异常发生时文件句柄不会被及时释放，可能造成资源泄漏。 | 改为 `with open(path, encoding="utf-8") as fh:`。 |
| 中 | `PY002` | L44 | 异常被静默吞掉 | except 分支只写了 pass，出错时没有任何日志，线上问题难以定位。 | 至少记录日志或重新抛出：`logger.exception("...")` 或 `raise`。 |
| 低 | `PY012` | L9 | 导入的 datetime 未被使用 | 无用的导入会增加阅读负担，也可能掩盖循环依赖。 | 删除该导入，或用 `__all__` 明确说明它是对外导出的接口。 |
| 低 | `PY009` | L12 | 函数 add_item 缺少文档字符串 | 公开函数没有说明参数、返回值与异常，接口意图不清晰。 | 补充 docstring，至少写清参数含义、返回值与可能抛出的异常。 |
| 低 | `PY010` | L12 | 函数 add_item 参数过多（7 个） | 参数列表过长会降低可读性，调用时容易传错顺序。 | 把相关参数收敛为 dataclass / 配置对象。 |
| 低 | `PY007` | L13 | 使用 == / != 与 None 比较 | None 是单例，用等号比较可能被自定义的 __eq__ 影响，也不符合 PEP 8。 | 改为 `is None` / `is not None`。 |
| 低 | `PY009` | L25 | 函数 load_inventory 缺少文档字符串 | 公开函数没有说明参数、返回值与异常，接口意图不清晰。 | 补充 docstring，至少写清参数含义、返回值与可能抛出的异常。 |
| 低 | `PY009` | L40 | 函数 run_report 缺少文档字符串 | 公开函数没有说明参数、返回值与异常，接口意图不清晰。 | 补充 docstring，至少写清参数含义、返回值与可能抛出的异常。 |
| 低 | `PY005` | L42 | 子进程调用未设置超时 | 外部命令挂起时，父进程会被无限期阻塞。 | 补充 `timeout=` 参数，并处理 `subprocess.TimeoutExpired`。 |
| 低 | `GEN001` | L48 | 单行超过 100 字符（119） | 过长的行在 code review、diff 与双栏编辑器中都难以阅读。 | 按语义换行，或拆分为多个表达式。 |
| 低 | `PY008` | L48 | 函数 summarize 偏长（36 行） | 函数超过 30 行后，阅读与单测成本明显上升。 | 考虑把内部逻辑按步骤抽取为独立函数。 |
| 低 | `PY010` | L48 | 函数 summarize 参数过多（7 个） | 参数列表过长会降低可读性，调用时容易传错顺序。 | 把相关参数收敛为 dataclass / 配置对象。 |
| 低 | `GEN001` | L71 | 单行超过 100 字符（103） | 过长的行在 code review、diff 与双栏编辑器中都难以阅读。 | 按语义换行，或拆分为多个表达式。 |
| 低 | `GEN002` | L86 | 遗留的 TODO / FIXME | 未完成的标记如果长期留在代码里，会逐步失去上下文。 | 补齐实现，或转成带负责人和截止时间的 issue。 |
| 低 | `GEN001` | L87 | 单行超过 100 字符（151） | 过长的行在 code review、diff 与双栏编辑器中都难以阅读。 | 按语义换行，或拆分为多个表达式。 |

## 优先修复顺序

1. **[高] `PY003` L33 使用了 eval()** —— 改用 `ast.literal_eval` 解析字面量，或改为显式的映射表 / 分发函数。
2. **[高] `PY004` L37 通过 shell 执行命令** —— 改用 `subprocess.run([...], check=True, timeout=...)` 以列表形式传参。
3. **[高] `PY001` L44 使用了裸 except** —— 改为捕获具体异常类型，例如 `except ValueError as exc:`；确实需要兜底时用 `except Exception`。
4. **[中] `PY006` L12 参数默认值使用了可变对象** —— 默认值改为 `None`，函数内部再 `if arg is None: arg = []`。
5. **[中] `PY013` L26 open() 未使用 with 语句** —— 改为 `with open(path, encoding="utf-8") as fh:`。

## 风险提示

存在 3 个高优先级问题，涉及正确性或安全性，建议在合并代码前修复。

> 本报告由静态分析工具基于真实代码生成，行号可直接跳转核对。
```

## 2. 对照样例：整洁代码

```bash
python main.py --provider mock "审查 examples/clean_sample.py"
```

```text
# 代码审查报告：`examples/clean_sample.py`

**综合评分：100/100（优秀）**

- 规模：61 行（代码 45 行，注释 0 行）
- 结构：4 个函数 / 1 个类 / 8 处导入
- 最长函数：16 行

## 结论

本次静态检查未发现明显问题。建议补充单元测试覆盖边界输入，并确认异常处理路径。
```

## 3. 代码解释（片段）

```bash
python main.py --provider mock "解释 examples/buggy_sample.py"
```

```text
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

## 需要注意的风险点

- L33 **使用了 eval()**：动态执行字符串代码，输入不可信时会直接造成任意代码执行。
- L37 **通过 shell 执行命令**：`os.system` 会把字符串交给 shell 解释，存在命令注入风险且无法拿到返回码。
- L44 **使用了裸 except**：会捕获所有异常（包括 KeyboardInterrupt、SystemExit），真实错误容易被掩盖。
```

## 4. 运行验证（片段）

```bash
python main.py --provider mock "运行 examples/buggy_sample.py"
```

```text
# 运行结果：`examples/buggy_sample.py`

- 退出码：0（执行成功）
- 耗时：111.0 ms

## 标准输出

(空)

## 标准错误

(空)

结论：程序正常结束，输出如上。
```

## 5. 测试脚手架（片段）

```bash
python main.py --provider mock "为 examples/buggy_sample.py 生成单元测试"
```

```python
"""由 Code Agent 生成的单元测试脚手架（离线模式）。"""
import importlib.util
import unittest
from pathlib import Path

TARGET = Path(__file__).with_name("buggy_sample.py")


def _load_module():
    spec = importlib.util.spec_from_file_location(TARGET.stem, TARGET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_add_item_normal_case(self):
        """TODO: 为 add_item() 构造正常输入并断言返回值。"""
        self.skipTest("待补充断言")
```
