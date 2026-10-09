# AGENTS.md

Authoritative dev/ops guidance lives in [`CLAUDE.md`](CLAUDE.md), [`QUICKSTART.md`](QUICKSTART.md),
and [`docs/`](docs/). Read those first. This file only adds Cursor Cloud specifics.

## Cursor Cloud specific instructions

Scope note: the Cloud VM is a **single generic Linux dev box**, not the "mini vs MacBook"
production split described in `CLAUDE.md`/`QUICKSTART.md`. The machine-boundary rules
(launchd, live order submission, Fubon SDK, `GOLDENSTOCKS_DATA_DIR`, SSH to mini) do **not**
apply here — none of that production infrastructure exists on the Cloud VM. Treat this as a
code/research/test box only.

### What's already set up (by the startup update script)

- Python venv at `.venv` (Python 3.12) with `requirements.txt` + `requirements-dev.txt` installed.
- `src/` is a **flat import root, not a package**: run scripts as `PYTHONPATH=src .venv/bin/python ...`.
  pytest already injects this via `pyproject.toml` (`pythonpath=["src"]`), so pytest needs no `PYTHONPATH`.
- One-time system dependency `python3.12-venv` (needed by `python3 -m venv`) is baked into the VM
  image, not the update script. If a fresh VM ever lacks it, `sudo apt-get install -y python3.12-venv`.

### How to verify the environment (no API keys, no DB needed)

The sanctioned first-run check (see `QUICKSTART.md` §3) needs no TEJ/FinMind keys and no `stocks.db`:

```bash
.venv/bin/ruff check src tests                                      # lint (CI parity: only E9/F63/F7/F82)
PYTHONPATH=src .venv/bin/python src/pipeline_gates.py list-mismatches  # config gate consistency
.venv/bin/pytest tests/ --ignore=tests/research/archive -q         # unit/integration suite
```

### Known pre-existing failures on a clean Cloud VM (NOT caused by your changes)

These fail out of the box and are **out of standard dev scope** — `main`'s CI is red for the same
reasons (verify with `gh run list --branch main`). Do not treat them as regressions:

- **Order-layer tests** (`test_*_order.py`, `test_tmf_*`, `test_dayflip_short_*`, `test_order_fubon_orders.py`,
  `test_tmf_fubon_live_book.py`, `test_songshan_copytrade_order.py`, `test_detach_gate_order.py`,
  `test_leading_dip_order.py`) require the **Fubon Neo SDK** (`fubon_neo`, normally in a separate
  `.venv-fubon`). That wheel is broker-provided and not installable here → `ModuleNotFoundError: fubon_neo`.
- **`test_finmind_tx_foreign_backtest.py`** is data-gated: it needs a populated `data/stocks.db`
  (multi-GB, not in the repo) and asserts on real rows.

To get a fully green run of the core suite, ignore those files (all are production/data-gated), e.g.
add `--ignore=tests/test_<name>.py` for each. That yields ~1270 passed / ~17 skipped.

### Running the product

This is a **batch research OS**, not a long-running web service. The core entrypoint is the daily
close pipeline (`scripts/daily_sync.sh`, full: `scripts/1630收盤雷達.command`), which produces markdown
briefs under `reports/daily/`. A real end-to-end data run needs `TEJ_API_KEY` + `FINMIND_TOKEN` in a
`.env` (template `.env.example`) **and** a populated `data/stocks.db` — none of which exist on the
Cloud VM, so the full pipeline cannot produce real output here. Report-*rendering* code paths (e.g.
`regime_daily_brief.render_regime_daily_markdown`) do run offline from an in-memory snapshot.

The checked-in dashboards are optional and data-dependent: `scripts/research/biglot_dashboard.py`
(port 8771) needs live JSONL caches from `GOLDENSTOCKS_DATA_DIR`; the port-8770 TMF sim server is
**not in the repo** (`reports/research/channel_lab/` is gitignored). Neither is needed to test the code.
