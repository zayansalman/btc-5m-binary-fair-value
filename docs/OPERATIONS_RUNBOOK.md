# Operations Runbook

This runbook is for the local BTC 5-minute binary fair-value strategy lab. The goal is to
make operation boring: visible state, bounded risk, and fast Stop behavior.
Paper mode is the default; live mode is strictly opt-in (see "Going live").

## Start Locally

```bash
./.venv/bin/python main.py
```

Open:

```text
http://127.0.0.1:7860
```

## Paper Trading

- Press **Start BTC Paper Bot** to begin the BTC 5-minute paper loop.
- Press **Stop** to halt new paper entries and force-close open simulated
  positions.
- Use **Refresh** if you want an immediate dashboard update between timer ticks.

## Health Checks

```bash
./.venv/bin/python tools/demo_snapshot.py
```

Expected:

- Risk state is `OK`, `IDLE`, or an explicit stale/feed state.
- Open positions are `0` or `1`.
- Activity feed contains BTC bot events.
- Start/Stop events are visible in structured logs and SQLite notifications.

## Common Issues

- If the dashboard port is busy, stop the old process or change
  `DASHBOARD_SERVER_PORT`.
- If no current BTC market is found, wait for the next 5-minute boundary and
  refresh.
- If public BTC spot data is unavailable, the paper loop surfaces the error in
  logs and dashboard detail instead of opening silent entries.

## Going Live

Live mode places REAL orders with REAL funds on the Polymarket CLOB. Read this
whole section before flipping the switch.

### Preconditions

- The paper bot has run cleanly on your machine (ticks flowing, no feed errors).
- Your Polymarket wallet (the funder) holds the USDC you are willing to lose.
- You know your signature type: `0` = plain EOA wallet, `1` = email/magic-link
  account (most common, the default), `2` = browser-wallet proxy.

### Launch steps

1. Edit `.env` (never commit it):

   ```bash
   BTC_BOT_MODE=live
   POLYMARKET_PRIVATE_KEY=0x...          # signing key — never logged, never committed
   POLYMARKET_FUNDER=0x...               # proxy wallet holding your USDC
   POLYMARKET_SIGNATURE_TYPE=1
   BTC_LIVE_CONFIRM=YES_I_UNDERSTAND     # exact phrase, typed by you
   ```

2. Start the app and press **Start** on the dashboard:

   ```bash
   ./.venv/bin/python main.py
   ```

3. Verify the dashboard says **LIVE — orders are real** and the activity feed
   shows `btc_live_started`. If either boot gate is missing, Start refuses with
   an explicit error and nothing runs — live never silently falls back to paper.

### Hard risk limits (enforced in code before every order)

| Limit | Env var | Default |
| --- | --- | --- |
| Max notional per trade | `BTC_LIVE_MAX_TRADE_USD` | $3 |
| Open positions | (fixed) | 1 |
| Daily realized-loss halt | `BTC_LIVE_DAILY_LOSS_HALT_USD` | $10 (UTC day, persisted) |
| Daily bankroll cap (sum of buys) | `BTC_LIVE_BANKROLL_CAP_USD` | $30 (UTC day, persisted) |
| Entry slippage guard (ask vs signal) | `BTC_LIVE_MAX_ENTRY_SLIPPAGE` | 0.02 |
| Exit fill wait before cancel/retry | `BTC_LIVE_EXIT_FILL_TIMEOUT_SECONDS` | 10s |

The daily loss halt and the daily bankroll cap are **persisted in SQLite and
reloaded at boot** — Stop/Start or a process restart inside the same UTC day
does NOT reset them. Realized PnL feeds the halt from CONFIRMED exit fills at
the executed order's limit price, never from paper-price estimates at
submission time.

A malformed risk-limit env value (e.g. `BTC_LIVE_MAX_TRADE_USD=O.50`) makes
live boot REFUSE with the exact parse error instead of silently falling back
to the looser default.

Blocked attempts are journaled to the `btc_live_orders` table with status
`BLOCKED` — check it if the bot seems quiet.

Note: Polymarket enforces a minimum order size (typically 5 shares). With a $3
per-trade cap, entries above roughly $0.60/share are blocked as below-minimum;
this is expected and journaled.

### Boot reconciliation (restarts are safe)

Every live boot, BEFORE any trading:

1. **All resting CLOB orders on the account are cancelled** (`cancel_all`).
   Use a dedicated bot wallet — manual orders from the same wallet would be
   cancelled too.
2. Any **open ledger position is re-adopted** from the `btc_live_orders`
   journal (token, entry price, exchange-confirmed fill size) so the normal
   exit path flattens it. Open rows with no live order behind them (paper
   artifacts, never-filled entries) are closed harmlessly with reason
   `RECONCILED_*`.
3. If the account state cannot be reconciled (CLOB unreachable, >1 open row),
   boot is REFUSED with instructions — the bot never trades on top of unknown
   exposure.

### Kill switch

```bash
touch data/KILL     # block all NEW entries NOW and cancel resting orders
rm data/KILL        # re-arm (the bot resumes on the next tick)
```

- The file is checked on every tick, before every entry, and once more
  immediately before the order is posted (covers the book-fetch window).
- **Exits stay allowed under kill**: flattening an open position only reduces
  exposure, and on a 5-minute binary a frozen position can become a 100%
  loss at resolution. If you want literally zero order flow, flatten manually
  in the Polymarket UI after touching the kill file.
- Deleting the file re-arms the handler: touching it again later cancels the
  orders resting at that moment again.

Drill this once before going live so you know it works.

### Stopping and flattening

- **Stop** on the dashboard sets the stop flag, then **waits for the runner
  thread to finish its own shutdown**: the runner cancels any resting entry
  order and flattens whatever filled through the live executor before it
  exits. The controller never drives the executor itself, so Stop cannot race
  the trading loop.
- A position whose live exit fails (no bid, CLOB error, unfilled sell) is
  **left OPEN in the ledger** — it is never paper-closed — and the Stop
  status tells you how many rows need manual flattening on Polymarket.
- The strategy always exits before window resolution (TIME exit at 45s), so
  there is no redemption path; if the process dies mid-position, just restart
  the bot — boot reconciliation cancels stale orders and re-adopts the
  position — or flatten manually in the Polymarket UI and close the row:

  ```bash
  sqlite3 data/btc_5m_binary_fair_value.db \
    "UPDATE btc_paper_positions SET state='closed', exit_reason='MANUAL' WHERE state='open'"
  ```

### Live audit trail

Every order, cancel, and blocked attempt lands in SQLite:

```bash
sqlite3 data/btc_5m_binary_fair_value.db \
  "SELECT created_at, intent, side, price, size, status, error FROM btc_live_orders ORDER BY id DESC LIMIT 20"
```

## Data

SQLite lives at `DB_PATH`, defaulting to `./data/btc_5m_binary_fair_value.db`.

The `data/` directory is local and gitignored.
