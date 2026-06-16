# Lessons

## Respect the existing UI architecture for dashboard work (2026-06-16)
**Pattern:** When adding any dashboard UI, conform to the established panel architecture instead of bolting on a bespoke surface:
- Pure `render(...) -> str` panel module in `btc_5m_fv/ops/dashboard/panels/` (no DB access in the panel).
- Read data in `ems.py` (or `panels/_data.py`) and pass it in; `ems.py` orchestrates and composes the grid.
- Reuse the theme CSS vocabulary (`card`, `card-h`, `de-kv`, `gr-btn`/`btn-ok`, `pill`, `mono`, `win`) and add any new classes in `static/style.css` matching the Bloomberg-EMS variables.
- Controls POST to a `/api/...` endpoint and call the existing global `refreshAll()`; persist to the `config` table; re-read per tick (mirror `refresh_overrides` / `set_paper_bypass_loss_halt`).
**Why:** Keeps the dashboard consistent, testable, and SSE-refreshable; avoids one-off patterns that drift from the rest of the EMS.
**How to apply:** Before building dashboard UI, read `panels/__init__.py`, `_shared.py`, an existing panel (e.g. `guardrails.py`), `ems.py`, `dashboard.js`, and `style.css`; then slot the new piece into those seams.

## Scope tightly; don't over-build (2026-06-16)
**Pattern:** Initial ask sounded like "max trade size + singleton/multiple mode + max positions"; after planning the full multi-position LiveExecutor refactor, the operator narrowed it to "just make max trade size settable in the UI, leave singleton."
**Why:** The money-path refactor (scalar→map LiveExecutor) was large and risky; the operator's real need was a single runtime knob.
**How to apply:** When a request spans a cheap change and an expensive architectural one, confirm scope before building the expensive part. A short clarifying question up front avoided a large unwanted diff here.

## A "smaller clip" can be an *unplaceable* clip — validate against the venue minimum (2026-06-17, #85)
**Pattern:** The #50 max-trade-size feature let the operator set any cap `0 < v ≤ 1000`, reasoning a value below the min-trade size "just gives a smaller fixed clip." It doesn't: a $1 cap at favourites (price ≥ 0.50) sizes every order below Polymarket's 5-share minimum, so the live executor blocked 100% of entries. Live trading was dead for a full session before anyone noticed.
**Why:** A per-trade *ceiling* below the sizing *floor* (`BTC_PAPER_MIN_TRADE_USD`) inverts the range; `notional_from_confidence` then pins every order to the floor → sub-minimum. The UI showed "min $5.00" but only as cosmetic text — no boundary actually enforced it (endpoint, HTML `min`, and gate read all let $1 through).
**How to apply:** Any operator-settable risk/sizing knob must be validated against the *venue* constraint it feeds, not just a sanity range. Enforce at every boundary (endpoint reject + gate read + UI widget), and make stored-state auto-heal so an already-persisted bad value doesn't keep biting after the fix ships. When a UI shows a "min/max", wire it to real validation — never leave it as a hint. See [[project-btc-5m-polymarket-bot]].
