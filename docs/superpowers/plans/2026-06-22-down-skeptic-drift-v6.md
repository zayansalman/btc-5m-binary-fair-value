# down_skeptic_drift_v6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a regime-aware Down-Skeptic shadow candidate (`down_skeptic_drift_v6`), trim the operator model roster (hide `fair_value_v0`/`cushion_favorite_v2` from the selector but keep logging them; remove `late_convergence_v3` entirely), with no live model flip.

**Architecture:** `down_skeptic_drift_v6` is a pure function layered on v0's side selection, identical to `down_skeptic_v4` except the edge toll flexes with v5's standardised-momentum regime; it reduces to v4 exactly at regime 0. The roster trim decouples the "logged" set (`_MODELS`) from a new operator-facing "selectable" set (`SELECTABLE_MODELS`).

**Tech Stack:** Python 3.11, pytest, FastAPI dashboard. Async `aiosqlite` for config (unchanged here — the signal functions are pure/sync).

## Global Constraints

- Python 3.11; async I/O elsewhere but the shadow signals are pure sync functions.
- BTC 5-minute Up/Down only; one open paper position at a time (unchanged).
- Agents NEVER flip the live gate or the active live model. `btc_model.active` stays `down_skeptic_v4`; the operator promotes v6 via the selector.
- `regime = clamp((drift_per_second / sigma_per_second) / regime_full_scale, -1, +1)`, bullish positive; `0.0` when `drift_per_second is None` or `sigma_per_second` is falsy/≤0.
- v6 defaults: `down_edge_premium=0.02`, `regime_full_scale=0.3` (copied from v4 and v5 respectively).
- Do not hand-edit `docs/FILE_MAP.md` or `<!-- GENERATED -->` blocks — regenerate via `tools/gen_docs.py`.
- Run tests with `./.venv/bin/python -m pytest`.

---

### Task 1: `down_skeptic_drift_v6` signal function

**Files:**
- Modify: `btc_bot/shadow/signals.py` (add function after `cushion_drift_v5`, ~line 330)
- Test: `tests/unit/test_shadow_signals.py` (add `TestDownSkepticDriftV6`, extend import)

**Interfaces:**
- Consumes: `strategy.signal_from_executable_edges`, `_clamp` (module-private, `signals.py:28`), `SnapshotView.{fair_up,up_ask,down_ask,drift_per_second,sigma_per_second,remaining_seconds}`.
- Produces: `down_skeptic_drift_v6(view: SnapshotView, params: strategy.StrategyParams, down_edge_premium: float = 0.02, regime_full_scale: float = 0.3) -> ShadowSignal | None`.

- [ ] **Step 1: Write the failing tests**

Add to the import block at the top of `tests/unit/test_shadow_signals.py`:

```python
from btc_bot.shadow.signals import (
    cushion_drift_v5,
    cushion_favorite_v2,
    down_skeptic_drift_v6,
    down_skeptic_v4,
    late_convergence_v3,
)
```

Append this class to `tests/unit/test_shadow_signals.py`:

```python
# ---------------------------------------------------------------------------
# down_skeptic_drift_v6  (regime-aware two-sided edge toll)
# ---------------------------------------------------------------------------


class TestDownSkepticDriftV6:
    def test_neutral_regime_equals_v4_up(self, params: strategy.StrategyParams) -> None:
        """drift=0 -> regime 0 -> same Up decision as down_skeptic_v4."""
        view = _view(
            up_ask=0.55, down_ask=0.46, fair_up=0.70,
            sigma_per_second=0.0003, drift_per_second=0.0,
        )
        v6 = down_skeptic_drift_v6(view, params)
        v4 = down_skeptic_v4(view, params)
        assert isinstance(v6, ShadowSignal) and isinstance(v4, ShadowSignal)
        assert (v6.side, v6.entry_price, v6.edge) == (v4.side, v4.entry_price, v4.edge)

    def test_neutral_regime_equals_v4_down_marginal(
        self, params: strategy.StrategyParams
    ) -> None:
        """drift=0 -> a marginal Down (edge 0.06 < 0.07) is vetoed, like v4."""
        view = _view(
            fair_up=0.42, up_ask=0.50, down_ask=0.52,
            sigma_per_second=0.0003, drift_per_second=0.0,
        )
        assert down_skeptic_v4(view, params) is None
        assert down_skeptic_drift_v6(view, params) is None

    def test_drift_none_equals_v4(self, params: strategy.StrategyParams) -> None:
        """Missing drift feed -> regime 0 -> Up passes through like v4."""
        view = _view(up_ask=0.55, down_ask=0.46, fair_up=0.70, drift_per_second=None)
        v6 = down_skeptic_drift_v6(view, params)
        assert isinstance(v6, ShadowSignal) and v6.side == "Up"

    def test_bear_regime_vetoes_thin_up_that_v4_takes(
        self, params: strategy.StrategyParams
    ) -> None:
        """Full bear regime tolls Up by +0.02; a 0.06-edge Up v4 takes is vetoed."""
        # edge_up = 0.62 - 0.56 = 0.06 (in [0.05, 0.07)); Down not executable.
        common = dict(fair_up=0.62, up_ask=0.56, down_ask=0.46, sigma_per_second=0.0003)
        v4_sig = down_skeptic_v4(_view(**common), params)
        assert isinstance(v4_sig, ShadowSignal) and v4_sig.side == "Up"  # v4 takes it
        bear = _view(**common, drift_per_second=-0.0003)  # drift/sigma=-1 -> regime -1
        assert down_skeptic_drift_v6(bear, params) is None  # Up bar 0.07 > 0.06 -> veto

    def test_bull_regime_vetoes_thin_down_that_v4_takes(
        self, params: strategy.StrategyParams
    ) -> None:
        """Full bull regime tolls Down by +0.04; a 0.08-edge Down v4 keeps is vetoed."""
        # edge_down = (1-0.38) - 0.54 = 0.08 (>= v4 bar 0.07, < v6 bull bar 0.09).
        common = dict(fair_up=0.38, up_ask=0.50, down_ask=0.54, sigma_per_second=0.0003)
        v4_sig = down_skeptic_v4(_view(**common), params)
        assert isinstance(v4_sig, ShadowSignal) and v4_sig.side == "Down"  # v4 keeps it
        bull = _view(**common, drift_per_second=0.0003)  # drift/sigma=+1 -> regime +1
        assert down_skeptic_drift_v6(bull, params) is None  # Down bar 0.09 > 0.08 -> veto

    def test_none_when_v0_declines(self, params: strategy.StrategyParams) -> None:
        """No executable quotes -> v0 picks no side -> None (v6 never forces a side)."""
        view = _view(up_ask=None, down_ask=None, drift_per_second=-0.0003)
        assert down_skeptic_drift_v6(view, params) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/unit/test_shadow_signals.py::TestDownSkepticDriftV6 -q`
Expected: FAIL — `ImportError: cannot import name 'down_skeptic_drift_v6'`.

- [ ] **Step 3: Implement the function**

Insert into `btc_bot/shadow/signals.py` immediately after the end of `cushion_drift_v5` (before any trailing module code):

```python
def down_skeptic_drift_v6(
    view: SnapshotView,
    params: strategy.StrategyParams,
    down_edge_premium: float = 0.02,
    regime_full_scale: float = 0.3,
) -> ShadowSignal | None:
    """v4's down-skeptic edge toll, made regime-aware and two-sided.

    Reuses v0's side selection (:func:`strategy.signal_from_executable_edges`)
    exactly like :func:`down_skeptic_v4`, then gates by an edge toll. v4 charges
    a fixed ``down_edge_premium`` on every Down pick — correct against the
    structural ``spot >= reference`` Up bias in a flat/up market, but backwards
    in a bearish regime, where it leans the book into the losing Up side. Here
    the toll flexes with the same standardised-momentum regime as
    :func:`cushion_drift_v5`:

    * ``regime = clamp((drift/sigma)/regime_full_scale, -1, +1)`` (bullish
      positive); ``0`` when the drift feed or volatility is unavailable.
    * ``down_extra = down_edge_premium * clamp(1 + regime, 0, 2)`` — Down's toll
      grows in a bull regime and shrinks to ``0`` in a full bear regime.
    * ``up_extra = down_edge_premium * clamp(-regime, 0, 1)`` — Up earns a toll
      only in a bear regime.

    At ``regime == 0`` (or no drift feed) ``up_extra == 0`` and
    ``down_extra == down_edge_premium`` -> the decision is **identical to**
    :func:`down_skeptic_v4`, which is therefore its exact control.
    """
    edge_up = view.fair_up - view.up_ask if view.up_ask is not None else None
    edge_down = (1.0 - view.fair_up) - view.down_ask if view.down_ask is not None else None

    side, confidence, _notional, reason = strategy.signal_from_executable_edges(
        edge_up,
        edge_down,
        view.remaining_seconds,
        view.up_ask,
        view.down_ask,
        params,
    )
    if side is None:
        return None

    if side == "Up":
        entry_price = view.up_ask
        edge = edge_up
        fair_prob = view.fair_up
    else:
        entry_price = view.down_ask
        edge = edge_down
        fair_prob = 1.0 - view.fair_up

    if entry_price is None or edge is None:
        return None

    drift = view.drift_per_second
    sigma = view.sigma_per_second
    if drift is None or not sigma or sigma <= 0:
        regime = 0.0
    else:
        regime = _clamp((drift / sigma) / regime_full_scale, -1.0, 1.0)

    up_extra = down_edge_premium * _clamp(-regime, 0.0, 1.0)
    down_extra = down_edge_premium * _clamp(1.0 + regime, 0.0, 2.0)

    if side == "Up" and edge < params.entry_edge_min + up_extra:
        return None
    if side == "Down" and edge < params.entry_edge_min + down_extra:
        return None

    extra = up_extra if side == "Up" else down_extra
    note = f"down-skeptic-drift regime={regime:+.2f} +{extra:.03f} on {side}; "
    return ShadowSignal(
        side=side,
        entry_price=entry_price,
        fair_prob=fair_prob,
        edge=edge,
        confidence=confidence,
        reason=f"{note}{reason}",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/unit/test_shadow_signals.py::TestDownSkepticDriftV6 -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add btc_bot/shadow/signals.py tests/unit/test_shadow_signals.py
git commit -m "feat(#100): regime-aware down_skeptic_drift_v6 signal"
```

---

### Task 2: Register v6, add `SELECTABLE_MODELS`, drop `late_convergence_v3` from runner

**Files:**
- Modify: `btc_bot/shadow/runner.py:101-145` (`_MODELS`, `MODEL_LABELS`, `MODEL_DESCRIPTIONS`, `CANDIDATE_SIGNALS`; add `SELECTABLE_MODELS`)
- Test: `tests/unit/test_shadow_runner.py` (model-list assertions; remove the late-convergence logging test)

**Interfaces:**
- Consumes: `signals.down_skeptic_drift_v6` (Task 1).
- Produces: `runner.SELECTABLE_MODELS: list[str] == ["down_skeptic_v4", "cushion_drift_v5", "down_skeptic_drift_v6"]`; `down_skeptic_drift_v6` present in `_MODELS`, `CANDIDATE_SIGNALS`, `MODEL_LABELS`, `MODEL_DESCRIPTIONS`; `late_convergence_v3` absent from all of those.

- [ ] **Step 1: Write/adjust the failing tests**

In `tests/unit/test_shadow_runner.py`, replace the registry assertion block (currently around lines 153-169 referencing `late_convergence_v3`) with:

```python
    assert runner.DEFAULT_MODEL == "fair_value_v0"
    # Logged set keeps the controls; late_convergence_v3 is gone.
    assert set(runner.MODEL_IDS) == {
        "fair_value_v0",
        "cushion_favorite_v2",
        "down_skeptic_v4",
        "cushion_drift_v5",
        "down_skeptic_drift_v6",
    }
    assert "late_convergence_v3" not in runner.MODEL_IDS
    # Operator-selectable set is the curated subset.
    assert runner.SELECTABLE_MODELS == [
        "down_skeptic_v4",
        "cushion_drift_v5",
        "down_skeptic_drift_v6",
    ]
    # Controls are logged but NOT selectable.
    assert "fair_value_v0" not in runner.SELECTABLE_MODELS
    assert "cushion_favorite_v2" not in runner.SELECTABLE_MODELS
    # v6 is live-dispatchable; v0 stays out (native path); late_conv is gone.
    assert "down_skeptic_drift_v6" in runner.CANDIDATE_SIGNALS
    assert "fair_value_v0" not in runner.CANDIDATE_SIGNALS
    assert "late_convergence_v3" not in runner.CANDIDATE_SIGNALS
    for mid in runner.MODEL_IDS:
        assert mid in runner.MODEL_LABELS
        assert mid in runner.MODEL_DESCRIPTIONS
    assert runner.candidate_signal("fair_value_v0", view, params) is None
```

Delete the `test_late_convergence_window` async test (currently ~lines 114-131) and its `late_convergence_v3` row assertions. In the remaining record-shadow logging test (~lines 95-98), change `assert "late_convergence_v3" not in rows` if present, and ensure it asserts `assert "down_skeptic_drift_v6" in rows` for a window where v0 picks a side.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/unit/test_shadow_runner.py -q`
Expected: FAIL — `AttributeError: module 'btc_bot.shadow.runner' has no attribute 'SELECTABLE_MODELS'` (and/or KeyErrors).

- [ ] **Step 3: Edit `runner.py`**

Replace `_MODELS` (lines 101-109) with (control first, late_conv removed, v6 added):

```python
_MODELS: dict[
    str, Callable[[SnapshotView, strategy.StrategyParams], ShadowSignal | None]
] = {
    "fair_value_v0": _v0_control,
    "cushion_favorite_v2": signals.cushion_favorite_v2,
    "down_skeptic_v4": signals.down_skeptic_v4,
    "cushion_drift_v5": signals.cushion_drift_v5,
    "down_skeptic_drift_v6": signals.down_skeptic_drift_v6,
}
```

Replace `MODEL_LABELS` (lines 121-127):

```python
MODEL_LABELS: dict[str, str] = {
    "fair_value_v0": "Fair-Value · Settle",
    "cushion_favorite_v2": "Cushion Favorite",
    "down_skeptic_v4": "Down-Skeptic",
    "cushion_drift_v5": "Cushion · Regime Drift",
    "down_skeptic_drift_v6": "Down-Skeptic · Regime Drift",
}
```

Replace `MODEL_DESCRIPTIONS` (lines 128-134):

```python
MODEL_DESCRIPTIONS: dict[str, str] = {
    "fair_value_v0": "v0 baseline · edge 0.045–0.07 · favorites ≥0.50 · hold→resolution",
    "cushion_favorite_v2": "v0 + cushion: spot clearly on the favoured side of the strike",
    "down_skeptic_v4": "v0 but Down needs +0.02 extra edge (prices the ≥-tie Up bias)",
    "cushion_drift_v5": "v0 + regime-adaptive cushion: drift/σ momentum shifts the Up/Down bar",
    "down_skeptic_drift_v6": "v4 but the edge toll flexes with drift/σ: bear → Up tolled, bull → Down",
}
```

Add `SELECTABLE_MODELS` right after `MODEL_IDS` (line 119):

```python
# Operator-selectable models for the dashboard dropdown. A curated subset of
# MODEL_IDS: the controls (fair_value_v0, cushion_favorite_v2) keep logging in
# _MODELS but are hidden from the selector. v0's native path remains the default.
SELECTABLE_MODELS: list[str] = [
    "down_skeptic_v4",
    "cushion_drift_v5",
    "down_skeptic_drift_v6",
]
```

Replace `CANDIDATE_SIGNALS` (lines 138-145):

```python
CANDIDATE_SIGNALS: dict[
    str, Callable[[SnapshotView, strategy.StrategyParams], ShadowSignal | None]
] = {
    "cushion_favorite_v2": signals.cushion_favorite_v2,
    "down_skeptic_v4": signals.down_skeptic_v4,
    "cushion_drift_v5": signals.cushion_drift_v5,
    "down_skeptic_drift_v6": signals.down_skeptic_drift_v6,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/unit/test_shadow_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add btc_bot/shadow/runner.py tests/unit/test_shadow_runner.py
git commit -m "feat(#100): register v6, add SELECTABLE_MODELS, drop late_convergence_v3 from runner"
```

---

### Task 3: Remove the `late_convergence_v3` symbol

**Files:**
- Modify: `btc_bot/shadow/signals.py` (delete `late_convergence_v3`, ~lines 99-162)
- Modify: `tests/unit/test_shadow_signals.py` (delete `TestLateConvergenceV3` and the import)

**Interfaces:**
- Produces: `late_convergence_v3` no longer exists anywhere (verified by grep in Step 4).

- [ ] **Step 1: Delete the test class and import**

Remove `late_convergence_v3` from the import in `tests/unit/test_shadow_signals.py` (leaving `cushion_drift_v5, cushion_favorite_v2, down_skeptic_drift_v6, down_skeptic_v4`). Delete the entire `class TestLateConvergenceV3:` block and its section comment.

- [ ] **Step 2: Delete the function**

In `btc_bot/shadow/signals.py`, delete the entire `def late_convergence_v3(...)` function and its section comment/docstring (it sits between `cushion_favorite_v2` and `down_skeptic_v4`).

- [ ] **Step 3: Verify nothing references it**

Run: `grep -rn "late_convergence" --include="*.py" . | grep -v ".venv"`
Expected: no output.

- [ ] **Step 4: Run the affected tests**

Run: `./.venv/bin/python -m pytest tests/unit/test_shadow_signals.py tests/unit/test_shadow_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add btc_bot/shadow/signals.py tests/unit/test_shadow_signals.py
git commit -m "refactor(#100): remove late_convergence_v3 candidate"
```

---

### Task 4: Selector dropdown + switch allow-list use `SELECTABLE_MODELS`

**Files:**
- Modify: `btc_5m_fv/ops/dashboard/panels/controls.py:74-78` (dropdown options + orphan guard)
- Modify: `btc_5m_fv/ops/dashboard/app.py:761-768` (validate against `SELECTABLE_MODELS`)
- Test: `tests/unit/test_dashboard.py`

**Interfaces:**
- Consumes: `runner.SELECTABLE_MODELS` (Task 2).
- Produces: dropdown renders only selectable models (plus the current active model if not selectable); `set_runtime_config("active_model", x)` rejects any `x not in SELECTABLE_MODELS`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_dashboard.py` (follow the file's existing render/post helpers; adapt names to those already imported there):

```python
def test_selector_lists_only_selectable_models():
    from btc_5m_fv.ops.dashboard.panels import controls
    from btc_bot.shadow import runner

    html = controls.render(active_model="down_skeptic_v4")  # adapt to real signature
    for mid in runner.SELECTABLE_MODELS:
        assert f"value='{mid}'" in html
    assert "value='fair_value_v0'" not in html
    assert "value='cushion_favorite_v2'" not in html
    assert "value='late_convergence_v3'" not in html


def test_selector_includes_orphaned_active_model():
    """If the active model is a non-selectable (e.g. a logged control), it still shows."""
    from btc_5m_fv.ops.dashboard.panels import controls

    html = controls.render(active_model="fair_value_v0")  # adapt to real signature
    assert "value='fair_value_v0'" in html
```

For the allow-list, add an async test mirroring the existing `active_model` POST test in `tests/unit/test_dashboard.py`: posting `{"key": "active_model", "value": "fair_value_v0"}` (or `"late_convergence_v3"`) returns a 4xx/error, while `"cushion_drift_v5"` succeeds. Use the same client fixture the file already uses.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/unit/test_dashboard.py -q -k "selectable or orphan or active_model"`
Expected: FAIL (v0 currently still appears / is accepted).

- [ ] **Step 3: Edit `controls.py`**

Replace the option-loop (lines 74-78) so it iterates a guarded selectable list:

```python
        # Operator-selectable models only; always include the current active
        # model so a logged-but-hidden control selection is never orphaned.
        selectable = list(_shadow_runner.SELECTABLE_MODELS)
        if active_model not in selectable:
            selectable = [active_model, *selectable]
        *(
            f"<option value='{mid}'{' selected' if mid == active_model else ''}>"
            f"{escape(_shadow_runner.MODEL_LABELS.get(mid, mid))}</option>"
            for mid in selectable
        ),
```

(Match the surrounding f-string/list construction style already in `controls.py`; the key change is iterating `selectable` instead of `_shadow_runner.MODEL_IDS`.)

- [ ] **Step 4: Edit `app.py`**

At line 766 change the validation set:

```python
    if key == "active_model":
        model = str(value)
        if model not in _shadow_runner.SELECTABLE_MODELS:
            # Hidden controls / unknown ids are not operator-selectable.
            return _error_response(f"unknown or non-selectable model: {model}")  # match existing error style
        await set_config(_shadow_runner.ACTIVE_MODEL_KEY, model)
```

(Use the file's existing error-return idiom — copy whatever the current `model not in MODEL_IDS` branch returns.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/unit/test_dashboard.py -q -k "selectable or orphan or active_model"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add btc_5m_fv/ops/dashboard/panels/controls.py btc_5m_fv/ops/dashboard/app.py tests/unit/test_dashboard.py
git commit -m "feat(#100): selector + allow-list use SELECTABLE_MODELS with orphan guard"
```

---

### Task 5: Docs regen, changelog, full suite, push & PR

**Files:**
- Modify: `CHANGELOG.md`, `tasks/todo.md`, `tasks/lessons.md` (if a correction surfaced)
- Regenerate: `docs/FILE_MAP.md` + `<!-- GENERATED -->` blocks via `tools/gen_docs.py`

- [ ] **Step 1: Regenerate machine docs**

Run: `./.venv/bin/python tools/gen_docs.py`
Expected: `docs/FILE_MAP.md` and generated summary blocks update (test count, no late_convergence module note if listed).

- [ ] **Step 2: Update CHANGELOG + todo**

Add a CHANGELOG entry under a new version bump: "Regime-aware Down-Skeptic (`down_skeptic_drift_v6`); model roster trimmed (controls hidden from selector, `late_convergence_v3` removed)." Add a review section to `tasks/todo.md`.

- [ ] **Step 3: Run the full suite + type check**

Run: `./.venv/bin/python -m pytest -q`
Expected: all pass (≈635 + new tests − removed late_conv tests).
Run: `./.venv/bin/python -m mypy btc_bot/shadow/signals.py btc_bot/shadow/runner.py` (match repo's mypy invocation).
Expected: clean.

- [ ] **Step 4: Commit, push, open PR to develop**

```bash
git add CHANGELOG.md tasks/todo.md docs/FILE_MAP.md
git commit -m "docs(#100): CHANGELOG + regenerated agent docs for v6 roster"
git push -u origin feature/100-down-skeptic-drift-v6
gh pr create --base develop --title "feat(#100): regime-aware down_skeptic_drift_v6 + roster trim" --body "Closes #100. Shadow-first; no live model flip. See docs/superpowers/specs/2026-06-22-down-skeptic-drift-v6-design.md."
```

- [ ] **Step 5: Confirm live posture unchanged**

Run: `sqlite3 data/btc_5m_binary_fair_value.db "SELECT value FROM config WHERE key='btc_model.active';"`
Expected: `down_skeptic_v4` (unchanged — operator promotes v6 manually).

---

## Self-Review

- **Spec coverage:** §2 model → Task 1. §3 roster wiring (`SELECTABLE_MODELS`, `_MODELS`, `CANDIDATE_SIGNALS`, labels, late_conv removal) → Tasks 2–4. §4 shadow-first/no-flip → Task 5 Step 5 + Global Constraints. §5 tests → Tasks 1,2,4. §6 out-of-scope reconciliation → untouched. ✓
- **Placeholder scan:** Test code in Task 4 notes "adapt to real signature" for `controls.render`/dashboard client — these are real-codebase-coupling notes, not placeholders; the implementer matches the existing helper names in `test_dashboard.py`. All signal/runner code is complete and literal. ✓
- **Type consistency:** `down_skeptic_drift_v6(view, params, down_edge_premium=0.02, regime_full_scale=0.3) -> ShadowSignal | None` used identically in Task 1 (def), Task 2 (registries). `SELECTABLE_MODELS: list[str]` consistent across Tasks 2/4. `regime`/`up_extra`/`down_extra` math identical to the spec table. ✓
