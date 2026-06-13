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

### Getting a key and funder (the real flow — there is NO key export)

The Polymarket product has no "export private key" feature. API trading uses
a wallet **you** control. The documented options:

| You have | signature type | key | funder |
|---|---|---|---|
| Nothing yet (recommended) | `3` (deposit wallet) | fresh EOA key, generated locally | relayer-deployed deposit wallet owned by that key |
| A browser wallet (MetaMask etc.) | `0` (EOA) | from your wallet app | the EOA itself (needs POL for gas + on-chain approvals) |
| A Gnosis Safe | `2` | the Safe owner key | the Safe address |
| An email-login UI account | n/a for API | — | move the funds out instead (withdraw) |

**Path A — you already have a funded Polymarket account (connected wallet, e.g. MetaMask):**
Use it in place; no fund movement, no deploy, no approvals (your proxy is
already set up from trading in the UI). The trading funds live in a
deterministic proxy controlled by your wallet key, so the funder and
signature type are auto-detected from the key:

```bash
# 1. Put your signer key in .env (MetaMask: Account details -> Show private key):
#       POLYMARKET_PRIVATE_KEY=0x...
# 2. Detect the funder proxy + signature type from on-chain balances:
./.venv/bin/python tools/live_detect_wallet.py
```

It derives every wallet the key could control (EOA / POLY_PROXY / Gnosis
Safe), reads each one's on-chain pUSD balance, and writes the funded one's
address + matching signature type into `.env`. **Security:** that key
controls your entire wallet, not just the trading balance — only use a key
whose wallet holds nothing you are not willing to expose on this machine.

**Path B — start fresh with an isolated deposit wallet (type 3)**, fully
scripted (the bot's key then controls only what you move to it):

```bash
# one-time: official py-sdk handles deposit-wallet deploy + gasless approvals
./.venv/bin/pip install --pre polymarket-client
./.venv/bin/python tools/live_setup.py
```

The script generates a key if `.env` has none, mints a Builder API Key from
that key (used only for the gasless deploy, then discarded), deploys the
deterministic deposit wallet, and writes the config straight into `.env`
(perms `0600`). The private key is **never printed** — it cannot leak into
scrollback or logs. There is no separate approval step: the collateral
allowance is set automatically the first time the bot connects to a funded
wallet (`update_balance_allowance`). `BTC_LIVE_CONFIRM` is deliberately not
written — you add that line yourself as the final go-live step.

**Funding:** send USDC/pUSD on Polygon to the printed funder address. Funds
sitting in an existing Polymarket UI account move with **Withdraw → paste
the funder address** — no key export needed anywhere.

### Launch steps

1. Run `tools/live_setup.py` (above) and put its output block in `.env`
   (never commit it). The key is never logged and never journaled.

2. Preflight — verifies the gate, credential derivation, CLOB reachability,
   and that the funder balance is actually visible to the CLOB:

   ```bash
   ./.venv/bin/python tools/live_preflight.py
   ```

   Do not launch on a NO-GO.

3. Start the app and press **Start** on the dashboard:

   ```bash
   ./.venv/bin/python main.py
   ```

4. Verify the dashboard says **LIVE — orders are real** and the activity feed
   shows `btc_live_started`. If any boot gate is missing, Start refuses with
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
- If the process dies mid-position, just restart the bot — boot
  reconciliation cancels stale orders and re-adopts the position — or flatten
  manually in the Polymarket UI and close the row:

  ```bash
  sqlite3 data/btc_5m_binary_fair_value.db \
    "UPDATE btc_paper_positions SET state='closed', exit_reason='MANUAL' WHERE state='open'"
  ```

### Settlement and redemption (BTC_EXIT_STYLE=settle, the default)

Settle-style positions ride to window resolution and never place exit
orders. The engine reads the Chainlink settlement (Up iff close ≥ open),
books the 1.00/0.00 outcome into the ledger and the daily-loss halt, and
frees the position slot.

- **Winning tokens are NOT auto-redeemed.** The USDC sits in resolved
  positions until you redeem them on Polymarket (portfolio → Claim). Redeem
  every few hours during live sessions so the bankroll keeps cycling — with
  a $30 bankroll and $1–5 entries, roughly 6–10 unredeemed wins will starve
  new entries.
- Losing tokens expire worthless; nothing to do.
- Legacy scalp behavior (intra-window TARGET/STOP/BAND exits, always flat
  before resolution) is available with `BTC_EXIT_STYLE=scalp` — note it
  soaked **negative** under honest fills and exists for experiments only.

### Live audit trail

Every order, cancel, and blocked attempt lands in SQLite:

```bash
sqlite3 data/btc_5m_binary_fair_value.db \
  "SELECT created_at, intent, side, price, size, status, error FROM btc_live_orders ORDER BY id DESC LIMIT 20"
```

## Data

SQLite lives at `DB_PATH`, defaulting to `./data/btc_5m_binary_fair_value.db`.

The `data/` directory is local and gitignored.
