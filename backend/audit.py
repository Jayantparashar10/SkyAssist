"""Hash-chained audit log.

Every decision, action, escalation and supervisor outcome is written to
``audit_log`` with ``hash = sha256(prev_hash + event + payload + ts)``, so
a tampered or reordered entry breaks the chain and ``verify_chain`` catches
it. Pure functions only: computing a chain link is arithmetic on strings,
not I/O. Writing the resulting row to ``audit_log`` is db.py's job —
executor.py calls ``next_link`` for the hash, then hands the row to the
store. Keeping the hashing itself free of the database means it's testable
(including tamper detection) without a connection.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable

GENESIS_HASH = "0" * 64


def _payload_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _ts_iso(ts: datetime | str) -> str:
    """A timezone-INDEPENDENT ISO string for a timestamp, so a hash computed
    at insert time (a tz-aware ``datetime`` with whatever offset the clock
    was in) still matches the hash recomputed at verify time.

    Postgres's ``TIMESTAMPTZ`` stores an instant, not an offset — it always
    round-trips a timestamp back as UTC regardless of the offset it was
    inserted with. Hashing the raw ``isoformat()`` string would make
    ``verify_chain`` report a broken chain for every entry that was ever
    written and re-read (same instant, different offset, different
    string) — this stops that by normalizing to UTC before hashing, both
    when the link is first created and every time it's re-verified.
    """
    if isinstance(ts, datetime):
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        return ts.isoformat()
    return str(ts)


def compute_hash(prev_hash: str, event: str, payload: dict, ts: datetime | str) -> str:
    material = f"{prev_hash}|{event}|{_payload_json(payload)}|{_ts_iso(ts)}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class AuditLink(dict):
    """A dict with the fields db.py needs to insert one audit_log row."""


def next_link(prev_hash: str, event: str, payload: dict, ts: datetime) -> AuditLink:
    return AuditLink(
        event=event,
        payload=payload,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, event, payload, ts),
        ts=ts,
    )


def verify_chain(entries: Iterable[dict]) -> bool:
    """``entries`` must be ordered oldest-first. Each entry needs
    event/payload/prev_hash/hash/ts (ts may be a datetime or an ISO string).
    Returns False on the first broken or reordered link.
    """
    prev = GENESIS_HASH
    for entry in entries:
        if entry["prev_hash"] != prev:
            return False
        expected = compute_hash(prev, entry["event"], entry["payload"], entry["ts"])
        if entry["hash"] != expected:
            return False
        prev = entry["hash"]
    return True
