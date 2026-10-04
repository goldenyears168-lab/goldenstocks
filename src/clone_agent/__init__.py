"""複製人交易代理人（階段 A：條件成立 → 跳確認鍵 → 人裁決）。

硬邊界：本套件**不得** import 富邦 SDK 或 `src/order/`，所以構造上無法送出委託單。
`tests/test_clone_agent_approval.py::test_no_order_capability_anywhere_in_package`
會掃描整個套件來釘住這件事。

規格 SSOT：`docs/trader-clone-agent-prd.md`
"""
