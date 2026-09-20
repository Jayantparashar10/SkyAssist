"""The fixed simulated clock (A-14: 10:00 IST). Every delay/deadline
calculation must use ``sim_now()``, never wall-clock time.

``sim_now()`` reads ``SIM_NOW`` fresh on every call so tests can
monkeypatch it per case.

``IST`` is exported because Postgres's ``TIMESTAMPTZ`` always round-trips
as UTC regardless of insert offset — any code formatting a booking's
``sched_dep``/``new_dep`` must call ``dt.astimezone(IST)`` first, or a
14:00 IST departure displays as 08:30.
"""

import os
from datetime import datetime, timedelta, timezone

DEFAULT_SIM_NOW = "2026-09-23T10:00:00+05:30"

IST = timezone(timedelta(hours=5, minutes=30))


def sim_now() -> datetime:
    return datetime.fromisoformat(os.environ.get("SIM_NOW", DEFAULT_SIM_NOW))
