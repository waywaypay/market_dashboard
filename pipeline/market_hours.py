"""US equity market clock — session windows in US/Eastern.

The pipeline runs around the clock (every ``BRIEF_REFRESH_MINUTES``, plus on
boot), but fresh news, filings and a pre-market quote tape are only *expected*
during the run-up to and through a trading session. Source health uses this to
tell a genuinely stale feed apart from one that is simply quiet because the
market is between sessions — otherwise the dashboard cries "stale"/"failed"
all weekend and every overnight, when a slow tape is normal, not a fault.

Scope: weekends are handled; market holidays are not (this stays
dependency-free). A holiday reads as a normal weekday here — acceptable,
because the worst case is the pre-existing behavior (an alarm on a quiet day),
never a false all-clear.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
PREMARKET_OPEN = time(4, 0)  # pre-market tape + the morning news cycle ramp up
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def next_market_open(now: datetime) -> datetime:
    """Next 9:30am US/Eastern at-or-after `now`, skipping weekends."""
    local = now.astimezone(MARKET_TZ)
    candidate = local.replace(
        hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
    )
    while candidate < local or candidate.weekday() >= 5:
        candidate = (candidate + timedelta(days=1)).replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute
        )
    return candidate


def is_quiet_period(now: datetime) -> bool:
    """True when the market is between sessions, so quiet news/filing feeds and
    an absent pre-market tape are expected rather than a fault.

    False only during the window a pre-market product actually watches: a
    weekday from the pre-market open (04:00 ET) through the close (16:00 ET).
    Everything else — overnight, evenings, weekends — reads as quiet."""
    local = now.astimezone(MARKET_TZ)
    if local.weekday() >= 5:  # Saturday / Sunday
        return True
    return not (PREMARKET_OPEN <= local.time() < MARKET_CLOSE)
