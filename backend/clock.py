"""The fixed simulated clock.

The data pack sets the exercise on Wed 23 Sep 2026 but gives no time of
day, so the time itself is an assumption (A-14 in ``assumptions.py``),
fixed at 10:00 IST. Every delay, elapsed-time or deadline calculation in
the agent must be computed against this moment, never against wall-clock
time — otherwise the three scenarios would silently give different answers
depending on when the demo is run.

``sim_now()`` reads ``SIM_NOW`` fresh on every call (rather than caching it
at import time) so tests can monkeypatch the environment variable per case.

``IST`` is exported for one specific reason: Postgres's ``TIMESTAMPTZ``
stores an instant, not an offset, and always round-trips a value back as
UTC regardless of what offset it was inserted with (psycopg reflects
whatever the session's timezone is, which defaults to UTC on Neon). Every
booking time in the data pack (18:40, 07:10, 14:00, ...) is stated in IST,
so any code formatting a booking's ``sched_dep``/``new_dep`` for a customer
must call ``dt.astimezone(IST)`` first — skipping that silently shows the
UTC wall-clock number instead (e.g. a 14:00 IST departure displayed as
08:30). ``policy.py`` and ``executor.py`` both do this before formatting.
"""

import os
from datetime import datetime, timedelta, timezone

DEFAULT_SIM_NOW = "2026-09-23T10:00:00+05:30"

IST = timezone(timedelta(hours=5, minutes=30))


def sim_now() -> datetime:
    return datetime.fromisoformat(os.environ.get("SIM_NOW", DEFAULT_SIM_NOW))
