"""Dashboard panels.

Each module here owns one EMS panel (ribbon, guardrails, strategy, market,
decision engine, performance, TCA, blotter). The orchestrator in ``ems.py``
loads data once and dispatches to ``render(...)`` on each panel — panels are
pure transforms from (data, context) → HTML string, with no DB access. Shared
helpers live in ``_shared.py``; data loaders live in ``_data.py``.
"""
