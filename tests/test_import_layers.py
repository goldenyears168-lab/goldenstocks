import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_import_layers", ROOT / "scripts" / "check_import_layers.py")
cil = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cil)


def _tree(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return d


class RepoRatchetTest(unittest.TestCase):
    def test_no_new_violations_vs_baseline(self):
        import json
        base = json.loads(cil.DEFAULT_BASELINE.read_text(encoding="utf-8"))
        errors, _ = cil.compare(cil.analyse(cil.DEFAULT_SRC), base)
        self.assertEqual(errors, [], "\n".join(errors))


class DetectionTest(unittest.TestCase):
    def _run(self, files, base=None):
        cur = cil.analyse(_tree(files))
        return cil.compare(cur, base or {"edges": {}, "lazy_only_edges": 0})[0]

    def test_l3_importing_research_is_strict(self):
        errs = self._run({"etf_daily_report.py": "import research.backtest.x\n",
                          "research/__init__.py": "", "research/backtest/__init__.py": "",
                          "research/backtest/x.py": ""})
        self.assertTrue(any(e.startswith("R2") for e in errs))

    def test_strategy_importing_order_is_strict_even_if_baselined(self):
        files = {"radar.py": "from order.fubon import x\n", "order/__init__.py": "", "order/fubon.py": ""}
        self.assertTrue(any(e.startswith("R1") for e in self._run(files)))
        base = {"edges": {"R1": ["radar -> order.fubon"]}, "lazy_only_edges": 0}
        self.assertTrue(any(e.startswith("R1") for e in self._run(files, base)))

    def test_flat_importing_research_can_be_baselined(self):
        files = {"brief.py": "from research.backtest.x import y\n", "research/__init__.py": "",
                 "research/backtest/__init__.py": "", "research/backtest/x.py": ""}
        self.assertTrue(any(e.startswith("R3") for e in self._run(files)))
        base = {"edges": {"R3": ["brief -> research.backtest.x"]}, "lazy_only_edges": 0}
        self.assertEqual(self._run(files, base), [])

    def test_top_level_cycle_flagged(self):
        errs = self._run({"a.py": "import b\n", "b.py": "import a\n"})
        self.assertTrue(any(e.startswith("R5") for e in errs))

    def test_lazy_import_not_counted_as_top_cycle_but_counted_in_budget(self):
        files = {"a.py": "import b\n", "b.py": "def f():\n    import a\n"}
        errs = self._run(files)
        self.assertFalse(any(e.startswith("R5") for e in errs))
        self.assertTrue(any(e.startswith("R6") for e in errs))


if __name__ == "__main__":
    unittest.main()
