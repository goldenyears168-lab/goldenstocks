#!/usr/bin/env python3
"""src/ import 層級棘輪檢查（ratchet）：現有違規記在 baseline，只許減、不許增。

規則（SSOT 說明見 docs/src-map.md「依賴規則」）：
  R1 非下單層模組不得 import order.* / tmf_channel*          （嚴格；2026-10 已清零，baseline 必為空）
  R2 L3 收盤主線不得 import research.* 或 order.*             （嚴格，baseline 必為空）
  R3 平坦 L4 模組不得 import research.*                       （baseline 容許既有）
  R4 下單層不得 import research.*                             （baseline 容許既有）
  R5 模組層級（top-level）import 不得成環                     （嚴格）
  R6 函式內（lazy）import 邊總數不得超過 baseline            （只許減）

用法：
  PYTHONPATH=src python3 scripts/check_import_layers.py            # 檢查，違規 exit 1
  python3 scripts/check_import_layers.py --update-baseline         # 只刪除已修掉的舊違規，絕不新增
  python3 scripts/check_import_layers.py --init-baseline           # 首次建立 baseline（會覆寫）
只用標準函式庫，不 import src 內任何模組（純 AST）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "src"
DEFAULT_BASELINE = ROOT / "config" / "import_layers_baseline.json"

L3 = {"etf_daily_report", "regime_daily_brief", "report_hygiene", "query_stock_prices", "sync_etf_holdings"}
ORDER_FAMILY = {"order", "tmf_channel"}


def discover(src: Path) -> dict[str, Path]:
    mods: dict[str, Path] = {}
    for dp, _dn, fn in os.walk(src):
        if "__pycache__" in dp:
            continue
        for f in fn:
            if f.endswith(".py"):
                p = Path(dp) / f
                rel = ".".join(p.relative_to(src).with_suffix("").parts)
                if rel.endswith(".__init__"):
                    rel = rel[: -len(".__init__")]
                mods[rel] = p
    return mods


def group(m: str) -> str:
    head = m.split(".")[0]
    if head in ORDER_FAMILY:
        return "order"
    if head == "research":
        return "research"
    if "." in m:
        return "pkg:" + head
    return "flat"


def build_edges(mods: dict[str, Path]):
    def resolve(x: str) -> str:
        while x and x not in mods:
            x = x.rpartition(".")[0]
        return x

    top: dict[str, set[str]] = defaultdict(set)
    lazy: dict[str, set[str]] = defaultdict(set)

    def names(n: ast.AST) -> list[str]:
        if isinstance(n, ast.Import):
            return [a.name for a in n.names]
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            return [n.module] + [f"{n.module}.{a.name}" for a in n.names]
        return []

    for m, p in mods.items():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue

        def walk_top(body):
            for n in body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                for x in names(n):
                    y = resolve(x)
                    if y and y != m:
                        top[m].add(y)
                for fld in ("body", "orelse", "finalbody"):
                    sub = getattr(n, fld, None)
                    if isinstance(sub, list):
                        walk_top(sub)
                for h in getattr(n, "handlers", []) or []:
                    walk_top(h.body)

        walk_top(tree.body)
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(fn):
                    for x in names(n):
                        y = resolve(x)
                        if y and y != m and y not in top[m]:
                            lazy[m].add(y)
    return top, lazy


def top_cycles(mods: dict[str, Path], top: dict[str, set[str]]) -> list[list[str]]:
    sys.setrecursionlimit(20000)
    idx: dict[str, int] = {}
    low: dict[str, int] = {}
    st: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []
    c = [0]

    def sc(v: str):
        idx[v] = low[v] = c[0]
        c[0] += 1
        st.append(v)
        on.add(v)
        for w in top.get(v, ()):
            if w not in idx:
                sc(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = []
            while True:
                w = st.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in mods:
        if v not in idx:
            sc(v)
    return sorted(out)


def analyse(src: Path) -> dict:
    mods = discover(src)
    top, lazy = build_edges(mods)
    all_edges = {(m, y) for m, ys in list(top.items()) + list(lazy.items()) for y in ys}
    r: dict[str, set[str]] = {"R1": set(), "R2": set(), "R3": set(), "R4": set()}
    for a, b in all_edges:
        ga, gb = group(a), group(b)
        e = f"{a} -> {b}"
        if a in L3 and gb in ("research", "order"):
            r["R2"].add(e)
        if ga != "order" and gb == "order":
            r["R1"].add(e)
        if ga == "flat" and gb == "research":
            r["R3"].add(e)
        if ga == "order" and gb == "research":
            r["R4"].add(e)
    lazy_only = sum(len(lazy[m] - top[m]) for m in mods)
    return {
        "edges": {k: sorted(v) for k, v in r.items()},
        "top_cycles": top_cycles(mods, top),
        "lazy_only_edges": lazy_only,
        "n_modules": len(mods),
    }


STRICT = {"R1", "R2"}


def compare(cur: dict, base: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    stale: list[str] = []
    for rule in ("R1", "R2", "R3", "R4"):
        allowed = set(base.get("edges", {}).get(rule, [])) if rule not in STRICT else set()
        now = set(cur["edges"][rule])
        for e in sorted(now - allowed):
            errors.append(f"{rule} 新違規：{e}")
        for e in sorted(allowed - now):
            stale.append(f"{rule} 已修掉可從 baseline 移除：{e}")
    if cur["top_cycles"]:
        errors.append(f"R5 模組層級循環：{cur['top_cycles']}")
    lim = base.get("lazy_only_edges")
    if lim is not None and cur["lazy_only_edges"] > lim:
        errors.append(f"R6 函式內 import 邊 {cur['lazy_only_edges']} > baseline {lim}")
    elif lim is not None and cur["lazy_only_edges"] < lim:
        stale.append(f"R6 函式內 import 邊降為 {cur['lazy_only_edges']}（baseline {lim}），可 --update-baseline")
    return errors, stale


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--init-baseline", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    a = ap.parse_args(argv)

    cur = analyse(a.src)
    if a.init_baseline:
        snap = {"edges": {k: v for k, v in cur["edges"].items() if k not in STRICT},
                "lazy_only_edges": cur["lazy_only_edges"],
                "note": "只許減不許增；新增違規請先修依賴方向，不要改這個檔。"}
        a.baseline.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"baseline 已建立：{ {k: len(v) for k, v in snap['edges'].items()} } lazy={snap['lazy_only_edges']}")
        return 0
    base = json.loads(a.baseline.read_text(encoding="utf-8")) if a.baseline.exists() else {"edges": {}}
    errors, stale = compare(cur, base)
    if a.update_baseline:
        if errors:
            print("有新違規，拒絕更新 baseline：", *errors, sep="\n  ")
            return 1
        for rule in ("R3", "R4"):
            base["edges"][rule] = sorted(set(base["edges"].get(rule, [])) & set(cur["edges"][rule]))
        base["lazy_only_edges"] = min(base.get("lazy_only_edges", cur["lazy_only_edges"]), cur["lazy_only_edges"])
        a.baseline.write_text(json.dumps(base, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"baseline 已收斂（移除 {len(stale)} 項）")
        return 0
    for s in stale:
        print("提示：", s)
    if errors:
        print("import 層級檢查失敗：", *errors, sep="\n  ")
        return 1
    print(f"import 層級檢查通過（{cur['n_modules']} 模組；R1={len(cur['edges']['R1'])} R3={len(cur['edges']['R3'])} "
          f"R4={len(cur['edges']['R4'])} 皆在 baseline 內）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
