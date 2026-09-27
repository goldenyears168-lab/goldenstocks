#!/usr/bin/env python3
"""biglot dashboard Phase 0：靜態分析 biglot_dashboard.py 111 個函式的
「讀寫哪些全域變數／呼叫哪些其他函式」，拿來核對路線圖草案的 18 模組邊界猜測
是否站得住腳——草案是讀一次程式碼歸納出來的，沒有實際跑過依賴圖，不能直接信。

純 AST 靜態分析，不 import、不執行任何程式碼，可以在生產機上放心對著正式檔案跑。

輸出兩份：
  - dep_graph.json：機器可讀，{function: {reads, writes, calls}}，供以後自動化
    核對模組邊界用。
  - 終端機摘要：全域變數 fan-out 排行（被最多函式碰的全域＝state.py 候選）、
    零全域依賴的函式清單（Phase 1「純葉節點抽離」的真候選，取代草案裡用讀一次
    程式碼猜的那份清單）。

用法：
    .venv/bin/python scripts/research/biglot_phase0/dep_graph.py \\
        scripts/research/biglot_dashboard.py --out dep_graph.json
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path


def _module_level_stmts(stmts):
    """遞迴展開模組層級的控制流容器(if/try/with/for/while)，但不進 def/class——
    biglot_dashboard.py 有幾個關鍵全域是 `try: from biglot_stock_info import
    INFO as STOCK_INFO ... except: STOCK_INFO = {}` 這種模組層級 try/except，
    只掃 tree.body 的第一層會漏掉這些名字（2026-09-27 準備 Phase 1 搬移時，
    人工核對 _stock_info_block 才發現 STOCK_INFO/STOCK_BLOCKS 沒被算進依賴，
    差點把一個實際上讀了兩個全域的函式誤判成零依賴的葉節點）。"""
    for node in stmts:
        yield node
        if isinstance(node, ast.Try):
            yield from _module_level_stmts(node.body)
            for h in node.handlers:
                yield from _module_level_stmts(h.body)
            yield from _module_level_stmts(node.orelse)
            yield from _module_level_stmts(node.finalbody)
        elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            yield from _module_level_stmts(getattr(node, "body", []))
            yield from _module_level_stmts(getattr(node, "orelse", []))


def _assign_target_names(target):
    """遞迴展開 tuple/list 解構賦值的目標名字——`A, B, C = f()` 的 target 是一個
    `ast.Tuple`，不是 `ast.Name`，原本的 `isinstance(t, ast.Name)` 檢查會整組漏掉
    （2026-09-27 手動搬移 render() 時發現：HIST_BIG/PREV_CLOSE/Y_PMLOW/UNI5/
    PE_TABLE/PE_PEERS/PE_GEN/PE_EPS/VOLRISK/VOLRISK_DATE 全部用這種解構賦值定義，
    十個全域被這個工具徹底漏算，之前每一次 dep_graph 執行的「reads」清單都是不完整的
    ——所幸這幾個名字剛好都在另一份手動核對過的「危險全域」清單裡，已個別正確處理，
    沒有造成實際搬移錯誤，但這個工具本身的 bug 必須修，不能留著繼續騙人）。"""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _assign_target_names(elt)


def module_level_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in _module_level_stmts(tree.body):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                names.update(_assign_target_names(t))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound != "*":
                    names.add(bound)
    return names


def top_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def analyze_function(fn: ast.FunctionDef, module_globals: set[str],
                      func_names: set[str]) -> dict:
    declared_global: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Global):
            declared_global.update(node.names)

    reads: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in module_globals:
            reads.add(node.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in func_names and node.func.id != fn.name:
                calls.add(node.func.id)

    return {
        "reads": sorted(reads),          # 包含 declared_global（Load context 也算讀）
        "writes": sorted(declared_global),  # 明確 global 宣告 = 有意圖要寫
        "calls": sorted(calls),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="biglot_dashboard.py 路徑")
    ap.add_argument("--out", default="dep_graph.json")
    args = ap.parse_args()

    src = Path(args.source).read_text(encoding="utf-8")
    tree = ast.parse(src, filename=args.source)

    module_globals = module_level_names(tree)
    funcs = top_level_functions(tree)
    func_names = set(funcs)
    # global 宣告可能指向「函式內第一次才建立」的全域（例如 PAGE 這種容器），
    # 補進 module_globals 才不會漏掉這些讀寫
    for fn in funcs.values():
        for node in ast.walk(fn):
            if isinstance(node, ast.Global):
                module_globals.update(node.names)

    graph = {name: analyze_function(fn, module_globals, func_names)
              for name, fn in funcs.items()}

    Path(args.out).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"寫入 {args.out}（{len(graph)} 個函式）")

    fan_out: dict[str, int] = defaultdict(int)
    for info in graph.values():
        for g in set(info["reads"]) | set(info["writes"]):
            fan_out[g] += 1

    print("\n=== 全域變數 fan-out 排行（被最多函式碰＝state.py 候選，前20）===")
    for name, n in sorted(fan_out.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {name}: {n} 個函式")

    leaves = [name for name, info in graph.items()
              if not info["reads"] and not info["writes"] and not info["calls"]]
    print(f"\n=== 零全域依賴、零呼叫其他頂層函式的函式（{len(leaves)} 個，"
          f"Phase 1 純葉節點真候選）===")
    for name in sorted(leaves):
        print(f"  {name}")

    print(f"\n=== 有全域依賴但零呼叫其他頂層函式（{sum(1 for i in graph.values() if not i['calls'] and (i['reads'] or i['writes']))} 個，次順位候選）===")
    for name, info in sorted(graph.items()):
        if not info["calls"] and (info["reads"] or info["writes"]):
            print(f"  {name}: reads={info['reads']} writes={info['writes']}")


if __name__ == "__main__":
    main()
