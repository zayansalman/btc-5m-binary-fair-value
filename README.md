# BTC 5m Binary Pricing Model — a Polymarket trading lab

A local research and execution stack for Polymarket's BTC 5-minute Up/Down binary
markets: a pricing model, a paper/live execution stack, a shadow forward-tester for
candidate strategies, a validated tick-replay backtester, and (as of #182) shadow-only
research into adjacent maker-quoting and copy-trading strategies across the venue's
wider 5-minute Up/Down crypto family.

Agent instructions and scope live in **[AGENTS.md](AGENTS.md)**; the routing map is
**[docs/CODE_MAP.md](docs/CODE_MAP.md)**. Read those first for "where do I make what
change."

## Status (current)

Active development. The project ran a 30-day BTC-only signal-race research phase
(June–July 2026), found no exploitable directional edge at retail latency net of the
venue's taker fee, and was archived 2026-07-10 on that basis — see
**[docs/archive/](docs/archive/)** for that full record (findings, timeline, pivot
memo, postmortem). It was reopened 2026-08-04 to pursue adjacent lines of work the
original research phase didn't test.

Current active line (issue #182, open): is a top Polymarket account's two-sided
maker-quoting strategy reproducible natively (`btc_bot/pairarb/`), and/or is that
account copy-tradeable (`btc_bot/pairarb/mirror.py` + `tools/copytrade_*.py`)? Both are
**shadow-only** — no live orders. A live copy-trade executor exists and is
multi-gated (`COPY_LIVE_CONFIRM`), but has not been armed: the shadow sample so far is
contradictory (an early 8-fill read showed 92% of target PnL captured; a later
~2,950-fill sample at scale went net negative) and needs reconciliation before that
decision is made. See `tasks/todo.md` for the live status and open items.

## Architecture

Two coupled trees plus a small shared foundation:

```
btc_bot/                  # the live loop + signal math
├── paper.py              #   tick loop, snapshots, settle-style position lifecycle
├── controller.py         #   start/stop, watchdog, silent-stop detector
├── strategy.py           #   pricing-model math + executable-edge signal (pure)
├── params.py             #   operator-tunable runtime params
├── shadow/                #   the BTC-only model race (see docs/archive/ for its verdict)
└── pairarb/               #   two-sided maker quoting + copy-trade research, shadow only (#182)

btc_5m_exec/              # execution / connectors / ops
├── core/                 #   domain types, interfaces, exceptions
├── strategy/  connectors/  storage/  backtest/
├── execution/            #   paper lifecycle + LIVE executor (multi-gated) + RiskGate
└── ops/dashboard/         #   FastAPI operator dashboard (SSE), panels, runtime controls

config.py  db.py  logging_setup.py   # foundation: env parsing, SQLite + migrations, structlog
tools/                    # research instruments and CLI runners
tests/                    # DB-isolated, network-free (see docs/FILE_MAP.md for current count)
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full engineering tour
(patterns, ops defense-in-depth, data layer, testing/CI).

## Running it

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[test]"
cp .env.example .env
./.venv/bin/python main.py          # dashboard at http://127.0.0.1:7860
DB_PATH=/tmp/t.db ./.venv/bin/python -m pytest tests/ -q   # DB-isolated
```

Dashboard defaults to paper trading. Operator presses ▶ Start / Stop. Live trading is
built, multi-gated (mode + confirm string + key + coherent wallet), and off by
default — the operator, never an agent, arms and launches it. See
**[docs/OPERATIONS_RUNBOOK.md](docs/OPERATIONS_RUNBOOK.md)**.

## Reading the record

- **`AGENTS.md`** — agent rules and scope fence (source of truth).
- **`docs/CODE_MAP.md`** / **`docs/FILE_MAP.md`** — generated routing map and module status.
- **`docs/ARCHITECTURE.md`** — the engineering tour.
- **`docs/archive/`** — the June–July 2026 BTC-only research phase: findings, timeline,
  pivot memo, postmortem. Historical, not current status.
- **`tasks/todo.md`** — current status and open items.
- **`tasks/lessons.md`** — accumulated reasoning-error lessons, kept live.
- **`tasks/archive/todo_history.md`** — closed historical build log (issues #20–#144).
- **`CHANGELOG.md`** — release history.

## License

MIT — see `LICENSE`.
