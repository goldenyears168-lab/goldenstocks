"""tmf_channel：僅保留 Fubon session 共用池（session_pool）。

TMF 引擎／研究函式庫已於 2026-10 移除（實彈引擎退役、研究線整條下架；歷史見 git）。
期貨收集器（futopt-*）仍透過 order.tmf_channel_{ledger,marketdata,ws_feed} 與本套件的
session_pool 取行情，故此核心保留。
"""
