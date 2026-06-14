"""Clear the adaptive auto-pause and resume entries (#36).

Use after reviewing WHY the strategy auto-paused (edge decay). Resuming is a
deliberate operator action — the controller never auto-resumes into a losing
regime on its own.

    .venv/bin/python tools/clear_auto_pause.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as _config  # noqa: E402  (loads .env)
from btc_bot.adaptive import (  # noqa: E402
    clear_auto_pause,
    is_paused,
    rolling_performance,
)


async def main() -> int:
    paused, reason = await is_paused()
    perf = await rolling_performance(
        _config.BTC_AUTO_PAUSE_WINDOW, _config.BTC_EXIT_STYLE
    )
    print(f"current: paused={paused} reason={reason!r}")
    print(
        f"rolling {perf['n']} trades: ROI {perf['roi'] * 100:+.1f}% | "
        f"win {perf['win_rate'] * 100:.0f}% | "
        f"brier {perf['brier'] if perf['brier'] is None else round(perf['brier'], 3)}"
    )
    if not paused:
        print("not paused — nothing to clear.")
        return 0
    await clear_auto_pause()
    print("auto-pause cleared; entries will resume on the next qualifying signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
