"""scripts/xs/*.xs 的靜態檢查（沒有 XS 編譯器；真正的編譯結果以 XQ 編輯器為準）。"""
import re
import unittest
from pathlib import Path

XS = sorted((Path(__file__).resolve().parents[1] / "scripts" / "xs").glob("*.xs"))
STD = ["收盤", "漲跌幅%", "量比", "前20日均量", "前20日均成交值(億)", "主力集中度1日%", "主力集中度5日%", "流動性閘門(1=通過)"]


def code_lines(text: str):
    for ln in text.splitlines():
        s = ln.split("//")[0].strip() if '"' not in ln.split("//")[0] else ln.strip()
        if s:
            yield s


class XsScriptsTest(unittest.TestCase):
    def test_have_scripts(self):
        self.assertGreaterEqual(len(XS), 20)

    def test_each_script(self):
        for p in XS:
            with self.subTest(p.name):
                t = p.read_text(encoding="utf-8")
                self.assertTrue(t.startswith("{@type:filter}"), "首行需 {@type:filter}")
                nocomment = "\n".join(re.sub(r"//.*", "", ln) if ln.lstrip().startswith("//") else ln for ln in t.splitlines())
                self.assertEqual(nocomment.count("("), nocomment.count(")"), "括號不平衡")
                self.assertEqual(nocomment.count('"') % 2, 0, "引號不成對")
                self.assertEqual(len(re.findall(r"\bbegin\b", nocomment)), len(re.findall(r"\bend\b", nocomment)), "begin/end 不成對")
                outs = re.findall(r'outputfield\((\d+),[^;]*?,\s*(\d+),\s*"([^"]+)"\);', t)
                idx = [int(o[0]) for o in outs]
                self.assertEqual(idx, list(range(1, len(idx) + 1)), "outputfield 編號需連續")
                self.assertEqual([o[2] for o in outs[:8]], STD, "標準欄 1-8 順序與名稱不可改")
                self.assertIn("minLots(", t, "需有流動性閘門輸入")
                self.assertRegex(t, r"證據分級：(T0|T2|T3|TX|E)", "需標證據分級")
                self.assertEqual(len({o[2] for o in outs}), len(outs), "欄名不可重複")
                self.assertIn("ret = 1", t)
                for ln in nocomment.splitlines():
                    s = ln.strip()
                    if not s or s.startswith("{@type") or s.startswith("//"):
                        continue
                    self.assertTrue(s.endswith(";") or s.endswith("begin") or s.startswith("end"), f"語句未以分號結尾：{s[:60]}")


if __name__ == "__main__":
    unittest.main()
