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
