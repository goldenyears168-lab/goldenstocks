"""biglot dashboard 重構：`_xq_style_block` 讀 `XQ_STYLE`/`VIXTWN`——這兩個是
docs/biglot-refactor-roadmap.md 列出的「每日整包重新賦值」全域之一，`ingest()`
的過日重載會用 `global XQ_STYLE; XQ_STYLE = _load_xq_style()` 整包換掉物件。

如果這裡跟 biglot/utils.py 一樣用 `from biglot_dashboard import XQ_STYLE`，
搬過來的名字只會抓到「當下那一刻」的參照，過日後 `biglot_dashboard.py` 自己那份
換了新物件，這裡卻還指著昨天的舊物件——這正是本文件路線圖裡標注過的
stale-reference 風險。

正確做法：只 `import biglot_dashboard`（模組本身，不指名字），函式本體內用
`biglot_dashboard.XQ_STYLE` 屬性存取——這是屬性查找，每次呼叫都會拿到當下
biglot_dashboard 模組裡最新的值，不管 ingest() 換過幾次日都不會過期。這個
patch 本身不需要改 ingest() 或建額外的 state.py，用
scripts/research/biglot_phase0/check_daily_rebind.py 的手法驗證過確實不會 stale。
"""
from __future__ import annotations

import html as html_mod

import biglot_dashboard


def _xq_style_block(sid):
    """XQ全球贏家風格欄位(2026-09-27 jack 交辦):盤後算好的技術/籌碼衍生欄,見
    compute_xq_style_metrics.py docstring 逐欄公式。純展示,不進分數。"""
    d = biglot_dashboard.XQ_STYLE.get(sid)
    emp_rev = biglot_dashboard.EMPLOYEE_REVENUE_XQ.get(sid)
    if not d and emp_rev is None:
        return ""
    f = lambda v, u="": f"{v:+.2f}{u}" if v is not None else "—"  # noqa: E731
    fa = lambda v, u="": f"{v:.2f}{u}" if v is not None else "—"  # noqa: E731
    asof = d.get("asof") if d else None
    holder_wk = d.get("holder_asof_week") if d else None
    # 三種更新頻率各自的「更新於」標籤(2026-09-27 jack 交辦:每欄要標最後更新時間,
    # 因為更新頻率不同——日頻技術/籌碼欄跟著 stock_daily_bars 收盤走、持股分散表是週頻、
    # 員工平均營業額是人工貼的靜態值,三者不能共用同一個時間戳,否則會誤導成「都是即時」)
    daily_tag = f"日頻·收盤{html_mod.escape(asof)}" if asof else "—"
    weekly_tag = f"週頻·集保{html_mod.escape(holder_wk)}" if holder_wk else "—"
    static_tag = f"人工·{html_mod.escape(biglot_dashboard.EMPLOYEE_REVENUE_ASOF)}"
    beta_asof = d.get("beta_asof") if d else None
    beta_tag = f"週頻·weekly-sync {html_mod.escape(beta_asof)}" if beta_asof else "—"
    items = []
    if d:
        items = [
            ("Beta(vs 加權指數)", fa(d.get("beta")), beta_tag),
            ("換手率%", fa(d["turnover_pct"], "%"), daily_tag),
            ("一週%", f(d["ret_chg5d_pct"], "%"), daily_tag),
            ("SMA(20日)", fa(d["sma_20d"]), daily_tag),
            ("EMA-SMA(20日)", f(d["ema_sma_20d_diff"]), daily_tag),
            ("MACD(DIF/DEA/HIST)", f"{fa(d['macd_dif'])}/{fa(d['macd_dea'])}/{fa(d['macd_hist'])}", daily_tag),
            ("歷史波動率%(20日年化)", fa(d["hist_vol_20d_pct"], "%"), daily_tag),
            ("集中度%(主力買賣超/當日量)", f(d["concentration_pct"], "%"), daily_tag),
            ("外資/投信/自營買賣超比%", f"{f(d['foreign_net_pct'])}/{f(d['trust_net_pct'])}/{f(d['dealer_net_pct'])}%", daily_tag),
            ("借券賣出餘額增減(1日/5日)", f"{f(d['sbl_sell_chg1d'])}/{f(d['sbl_sell_chg5d'])}", daily_tag),
            ("當沖比例%", fa(d["daytrade_pct"], "%"), daily_tag),
            ("外資持股比例%(水位)", fa(d["foreign_holding_pct"], "%"), daily_tag),
            ("鉅額交易(量/金額/筆數)",
             (f"{d['block_volume']:,.0f}股/{d['block_amount']:,.0f}元/{d['block_count']:.0f}筆"
              if d.get("block_count") else "今日無"), daily_tag),
            ("800大戶持股%(週變化)", f"{fa(d['big800_holder_pct'], '%')}({f(d['big800_holder_pct_chg_w'])})", weekly_tag),
            ("10張以下散戶持股%(週變化)", f"{fa(d['retail10_holder_pct'], '%')}({f(d['retail10_holder_pct_chg_w'])})", weekly_tag),
        ]
    trs = "".join(f"<tr><td class='k'>{html_mod.escape(k)}</td><td>{v}</td>"
                  f"<td class='asof'>{tag}</td></tr>" for k, v, tag in items)
    emp_row = (f"<tr><td class='k'>員工平均營業額</td><td>{emp_rev:.2f}(未自算)</td>"
               f"<td class='asof'>{static_tag}</td></tr>" if emp_rev is not None else "")
    vix = biglot_dashboard.VIXTWN or {}
    vix_row = ""
    if vix.get("close") is not None:
        vix_chg = f(vix.get("chg_pct"), "%")
        vix_tag = f"日頻·大盤(非個股)·{html_mod.escape(vix['asof'])}"
        vix_row = (f"<tr><td class='k'>台灣VIX(VIXTWN,大盤情緒非個股)</td>"
                   f"<td>{vix['close']:.2f}({vix_chg})</td><td class='asof'>{vix_tag}</td></tr>")
    return (f"<div class='xqstyle'><div class='xqhead'>XQ全球贏家風格欄位</div>"
            f"<table class='xqtbl'><thead><tr><th></th><th>值</th><th>更新於</th></tr></thead>"
            f"<tbody>{trs}{emp_row}{vix_row}</tbody></table>"
            "<div class='xqnote'>日頻/週頻欄由 scripts/research/compute_xq_style_metrics.py 盤後批次算,"
            "已排進 daily_sync.sh（RUN_XQ_STYLE_METRICS,見 src/pipeline_gates.py），跟著收盤管線每日推進；"
            "不進分數。集中度%=三大法人合計買賣超÷當日成交量×100(jack 2026-09-27 確認公式)；"
            "外資/投信/自營買賣超比%為同一慣例類推,分母是否與XQ相同未逐一驗證；"
            "外資持股比例%是水位(存量),外資買賣超比%是流量(當天買賣),兩者互補非重複；"
            "借券賣出餘額用 sbl_balance(TWT93U真放空口徑),非融券/借券餘額(TWT72U)；"
            "當沖比例%自己用daytrade_volume÷當日成交量重算,不用該表原始欄位(常是NULL)；"
            "鉅額交易是稀疏事件,多數日子「今日無」是正常狀態不是缺資料；"
            "800大戶/10張以下散戶持股%取自TWSE集保股權分散表,PIT只用已公布最近一週；"
            "台灣VIX(VIXTWN)是大盤層級指標,42檔個股頁面顯示的是同一組數字,不是個股專屬；"
            "Beta(stock_beta,yahoo_computed vs ^TWII)每週靠 weekly-sync launchd job(週日20:00,"
            "2026-09-27新掛,根治scripts/weekly_sync.sh先前沒人排程的問題)更新一次,非日頻,"
            "且該表本身不是時間序列(每次resync覆蓋同一列),故只在最新交易日那列才有值；"
            "員工平均營業額暫沿用XQ截圖數字,FinMind查無員工人數對應資料源，未自算。</div></div>"
            "<style>.xqstyle{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 10px;"
            "margin-bottom:8px;font-size:12px}.xqstyle .xqhead{color:#79c0ff;font-weight:700;margin-bottom:4px}"
            ".xqtbl{border-collapse:collapse}.xqtbl td,.xqtbl th{padding:1px 10px 1px 0;text-align:left}"
            ".xqtbl th{color:#8b949e;font-weight:400;font-size:10px}"
            ".xqtbl td.k{color:#8b949e;white-space:nowrap}"
            ".xqtbl td.asof{color:#6e7681;font-size:10px;white-space:nowrap}"
            ".xqstyle .xqnote{color:#8b949e;font-size:11px;margin-top:4px;line-height:1.5}</style>")
