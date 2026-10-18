# tmf_channel（精簡核心）

2026-10：TMF 實彈引擎與研究函式庫（causal_engine／engine／nq_gate／tick_index／vpin 等）
整條移除，歷史請查 git。僅保留供期貨收集器共用的 Fubon session 池：

- `session_pool.py` — 單一 Fubon session 共用
- `order/tmf_channel_{ledger,marketdata,ws_feed}.py` — 交易日換算、前月合約解析、行情 ws

不要在此新增策略或下單邏輯。
