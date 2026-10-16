"""UI 代幣一致性:ui.css 的數值必須等於 docs/ui-standard.md 預設值;頁面內 var(--名稱,備援值) 的備援值必須等於代幣值。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "scripts" / "research" / "biglot" / "static" / "ui.css"

EXPECTED = {
    "price-up": "#ff7b72", "price-down": "#3fb950", "price-flat": "#484f58",
    "limit-up-bg": "#d1242f", "limit-down-bg": "#1a7f37",
    "status-ok": "#3fb950", "status-warn": "#e3b341", "status-error": "#f85149", "status-info": "#79c0ff",
    "flow-big": "#ff7b72", "flow-retail": "#58a6ff", "flow-mid": "#8b949e", "flow-warrant": "#d2a8ff",
    "bg": "#0d1117", "surface": "#161b22", "border": "#30363d", "border-soft": "#21262d",
    "text": "#c9d1d9", "text-strong": "#e6edf3", "text-muted": "#8b949e", "text-dim": "#484f58", "link": "#79c0ff",
}
USE = re.compile(r"var\(--([a-z-]+),\s*(#[0-9a-fA-F]{6})\)")


def tokens():
    return {m.group(1): m.group(2).lower() for m in re.finditer(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", CSS.read_text(encoding="utf-8"))}


class UiTokenTests(unittest.TestCase):
    def test_values_match_standard(self):
        self.assertEqual(tokens(), EXPECTED)

    def test_price_family_distinct_from_status(self):
        t = tokens()
        self.assertNotEqual(t["price-up"], t["status-error"])        # 漲色 ≠ 錯誤色
        self.assertNotEqual(t["flow-mid"], t["flow-warrant"])

    def test_fallbacks_match_tokens(self):
        t = tokens()
        files = list((ROOT / "scripts" / "research" / "biglot").glob("*.py")) + [ROOT / "scripts" / "research" / "biglot_dashboard.py"] + list((ROOT / "src" / "clone_agent").glob("*.py"))
        seen = 0
        for f in files:
            for m in USE.finditer(f.read_text(encoding="utf-8")):
                seen += 1
                self.assertIn(m.group(1), t, f"{f.name}: 未知代幣 --{m.group(1)}")
                self.assertEqual(m.group(2).lower(), t[m.group(1)], f"{f.name}: --{m.group(1)} 備援值與 ui.css 不同")
        self.assertGreater(seen, 10)                                   # 確認真的有頁面在引用


if __name__ == "__main__":
    unittest.main()
