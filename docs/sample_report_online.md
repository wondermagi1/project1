# 接入真实大模型的样例输出（DeepSeek）

本文件是 `.env` 中配置好 DeepSeek 密钥后，运行下面命令得到的**真实输出**（未经人工修改）：

```bash
python main.py "审查 examples/buggy_sample.py"
```

与离线模式（见 `docs/sample_report.md`）相比，真实模型会自行决定调用哪些工具，
并额外发现静态规则覆盖不到的问题——例如「参数声明了却从未生效」的假接口、
以及通过实际运行代码验证出来的可变默认参数污染。

---
# 代码审查报告：`examples/buggy_sample.py`

## 1. 结论与评分

**评分：32 / 100（较差，存在明显的正确性与安全风险）**

文件共 88 行、7 个函数，无类。整体是"库存 + 订单"的示例模块，但其中混入了 3 个高危安全问题（`eval`、`os.system`、裸 `except`）、1 个会导致数据串味的可变默认参数缺陷，以及 2 个"参数声明了但完全没生效"的隐性 bug。静态分析共报出 19 个问题（高 3 / 中 3 / 低 13），我另外通过实际运行验证了 4 个静态工具未覆盖的行为缺陷。

---

## 2. 问题清单

### 高危（安全 / 正确性）

| 严重度 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| 高 | L33 `parse_expression` | 直接 `eval(expression)` | 输入不可信时任意代码执行，可读写文件、发起网络请求 | 改 `ast.literal_eval`；若需运算，用显式映射表或 `operator` 分发 |
| 高 | L37 `backup_inventory` | `os.system("cp " + path + " " + path + ".bak")` 字符串拼接进 shell | 路径含空格/`;`/`&&` 时命令注入；且拿不到返回码，失败静默 | 改 `subprocess.run(["cp", path, path + ".bak"], check=True, timeout=30)`；纯拷贝可直接用 `shutil.copy2` |
| 高 | L44 `run_report` | 裸 `except:` + `pass` | 吞掉 `KeyboardInterrupt`/`SystemExit`，且出错无任何日志，线上无法定位 | 捕获具体异常（如 `subprocess.CalledProcessError`、`FileNotFoundError`），并 `logger.exception(...)` 或 `raise` |

### 中危（行为缺陷，已实测复现）

| 严重度 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| 中 | L12 `add_item(inventory={})` | 可变默认参数 | **实测确认**：两次独立调用返回同一个 dict（`a is b == True`），第二次调用会带上第一次的数据，跨调用数据污染 | 默认值改 `None`，函数内 `if inventory is None: inventory = {}` |
| 中 | L26 `load_inventory` | `open()` 未用 `with` | 解析异常时句柄不释放，长期运行会耗尽 fd | `with open(path, encoding="utf-8") as fh: return json.load(fh)` |
| 中 | L48 `summarize` | `sort_key` 参数声明了但从未使用（L73 只做 `lines.sort()`） | **实测确认**：`sort_key="price"` 与 `sort_key="name"` 返回完全相同的顺序，调用方以为按价格排序，实际按整行字符串排 | 改为 `lines.sort(key=..., reverse=reverse)`，或删除该参数避免误导 |
| 中 | L87 `format_report` | `width` 参数声明了但从未使用 | **实测确认**：`width=10` 与 `width=200` 输出完全一致，参数是"假接口" | 实现换行逻辑（`textwrap.wrap`），或移除参数 |

### 低危（可维护性 / 规范）

| 严重度 | 位置 | 问题 | 影响 | 建议 |
|---|---|---|---|---|
| 低 | L9 | `datetime` 导入未使用 | 阅读负担，可能掩盖循环依赖 | 删除 |
| 低 | L13 | `inventory == None` | 不符合 PEP 8，可能被自定义 `__eq__` 影响 | 改 `is None` |
| 低 | L42 | `subprocess.run` 无 `timeout` | 子进程挂起会无限阻塞父进程 | 加 `timeout=` 并处理 `TimeoutExpired` |
| 低 | L48 | 函数 36 行、7 个参数、单行 119 字符 | 阅读与单测成本高 | 按职责拆分（统计 / 渲染 / 排序），参数收敛为 dataclass |
| 低 | L12 | `add_item` 7 个参数、无 docstring | 调用易传错顺序，接口意图不明 | 用 dataclass 承载 item 字段，补 docstring |
| 低 | L25 / L40 | `load_inventory`、`run_report` 无 docstring | 异常行为（抛什么、返回什么）不明确 | 补 docstring，写明异常与返回值 |
| 低 | L71 / L87 | 单行 103 / 151 字符 | diff 与双栏编辑器难读 | 按语义换行 |
| 低 | L86 | 遗留 `# TODO` | 长期留存会丢失上下文 | 转成带负责人与截止时间的 issue |

> 补充说明：`run_report` 实测返回 `b''` 而非 `None`——因为 `report.py` 不存在时 `subprocess.run` 本身不抛异常，只是 stderr 有内容。这比"返回 None"更隐蔽：**调用方拿到空 bytes 会误以为报告为空，而不是执行失败**。建议加 `check=True` 让失败显式化。

---

## 3. 优先修复顺序

1. **L33 `eval`** —— 唯一能直接导致远程代码执行的入口，最高优先级。
2. **L37 `os.system`** —— 命令注入 + 静默失败。
3. **L12 可变默认参数** —— 已实测的数据污染，且是"看起来能跑"的隐性 bug。
4. **L44 裸 except + pass** —— 掩盖上面所有问题的现场，修完它才能发现其他故障。
5. **L48 `sort_key` / L87 `width` 假参数** —— 接口契约与实现不符，会误导调用方。
6. **L26 `with open`** —— 资源泄漏。
7. 其余低危项（docstring、行长、无用导入、TODO）批量清理。

---

## 4. 修改示例

```python
import ast
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Item:
    name: str
    price: float = 0
    quantity: int = 0
    category: str | None = None
    supplier: str | None = None
    note: str | None = None


def add_item(inventory: dict | None = None, item: Item | None = None) -> dict:
    """把 item 写入 inventory 并返回。

    inventory 为 None 时新建一个 dict，避免可变默认参数被跨调用共享。
    """
    if inventory is None:
        inventory = {}
    if item is None:
        raise ValueError("item 不能为空")
    inventory[item.name] = {
        "price": item.price,
        "quantity": item.quantity,
        "category": item.category,
        "supplier": item.supplier,
        "note": item.note,
    }
    return inventory


def load_inventory(path: str) -> dict:
    """读取 JSON 库存文件；文件不存在或格式错误时抛出对应异常。"""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def parse_expression(expression: str):
    """只解析字面量，拒绝函数调用与属性访问。"""
    return ast.literal_eval(expression)


def backup_inventory(path: str) -> None:
    """复制备份文件，失败时抛出异常而不是静默返回。"""
    shutil.copy2(path, path + ".bak")


def run_report(mode: str, timeout: int = 30) -> bytes:
    """执行 report.py，失败或超时时抛出异常并记录日志。"""
    try:
        result = subprocess.run(
            ["python", "report.py", mode],
            capture_output=True,
            check=True,
            timeout=timeout,
        )
        return result.stdout
    except subprocess.CalledProcessError:
        logger.exception("report.py 执行失败, mode=%s", mode)
        raise
    except subprocess.TimeoutExpired:
        logger.exception("report.py 超时, mode=%s", mode)
        raise
```

`summarize` 的排序修复（关键一行）：

```python
# 原：lines.sort(reverse=reverse)  —— sort_key 被忽略
lines.sort(key=lambda row: row[0], reverse=reverse)  # 或按 sort_key 映射到对应字段
```

---

## 5. 需要你确认的两点

1. `sort_key` 和 `width` 是**打算实现但漏了**，还是**历史遗留应删除**？这决定是补实现还是删参数。
2. 是否需要我基于修复后的版本生成一套 pytest 用例（覆盖可变默认参数、`sort_key` 排序、`run_report` 异常路径、`summarize` 的 `include_zero`/`limit` 分支）？我可以直接写入 `tests/test_buggy_sample.py`，不会覆盖已有文件。
