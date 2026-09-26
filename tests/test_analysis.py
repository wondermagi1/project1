"""静态分析引擎的单元测试。"""

from __future__ import annotations

import textwrap
import unittest

from agent.analysis import analyze_source, guess_language, summarize_findings

DIRTY_CODE = textwrap.dedent(
    '''
    """示例模块。"""
    import os
    import json
    from math import *


    def add_item(bucket={}, value=None):
        if value == None:
            return bucket
        bucket[value] = True
        return bucket


    def load(path):
        handle = open(path, "r")
        data = handle.read()
        return data


    def run(command):
        try:
            os.system(command)
        except:
            pass


    def parse(text):
        return eval(text)
    '''
)


class AnalysisTests(unittest.TestCase):
    def test_guess_language(self) -> None:
        self.assertEqual(guess_language("a.py"), "python")
        self.assertEqual(guess_language("a.TS"), "typescript")
        self.assertEqual(guess_language("a.unknown"), "text")

    def test_detects_common_python_issues(self) -> None:
        report = analyze_source(DIRTY_CODE, "dirty.py")
        codes = {finding["code"] for finding in report["findings"]}
        for expected in ("PY001", "PY002", "PY003", "PY004", "PY006", "PY007", "PY009", "PY011", "PY012", "PY013"):
            self.assertIn(expected, codes, f"缺少 {expected} 检查结果")
        self.assertEqual(report["parse_error"], None)
        self.assertLess(report["score"], 60)

    def test_findings_have_line_numbers_and_suggestions(self) -> None:
        report = analyze_source(DIRTY_CODE, "dirty.py")
        for finding in report["findings"]:
            self.assertGreaterEqual(finding["line"], 1)
            self.assertIn(finding["severity"], {"high", "medium", "low"})
            self.assertTrue(finding["message"])
        self.assertTrue(any(finding["suggestion"] for finding in report["findings"]))

    def test_clean_code_scores_high(self) -> None:
        clean = textwrap.dedent(
            '''
            """示例模块。"""


            def add(left: int, right: int) -> int:
                """返回两数之和。"""

                return left + right
            '''
        )
        report = analyze_source(clean, "clean.py")
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["score"], 100)
        self.assertEqual(report["grade"], "优秀")

    def test_syntax_error_is_reported_gracefully(self) -> None:
        report = analyze_source("def broken(:\n    pass\n", "broken.py")
        self.assertIsNotNone(report["parse_error"])
        self.assertEqual(report["findings"][0]["code"], "SYN001")
        self.assertEqual(report["findings"][0]["severity"], "high")

    def test_symbols_and_metrics(self) -> None:
        source = textwrap.dedent(
            '''
            """模块。"""


            class Store:
                """仓库。"""

                def put(self, item):
                    """写入。"""

                    return item


            def helper(value=1):
                """辅助函数。"""

                return value
            '''
        )
        report = analyze_source(source, "symbols.py")
        self.assertEqual(report["metrics"]["classes"], 1)
        self.assertEqual(report["metrics"]["functions"], 2)
        names = {item["name"] for item in report["symbols"]["functions"]}
        self.assertEqual(names, {"put", "helper"})

    def test_summarize_findings(self) -> None:
        report = analyze_source(DIRTY_CODE, "dirty.py")
        summary = summarize_findings(report["findings"])
        self.assertEqual(summary["total"], len(report["findings"]))
        self.assertGreater(summary["high"], 0)


if __name__ == "__main__":
    unittest.main()
