"""Hash-chained audit log: ``hash = sha256(prev_hash + event + payload + ts)``,
so a tampered or reordered entry breaks the chain. Pure functions only —
executor.py calls ``next_link`` for the hash, db.py writes the row.
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
    """Normalizes to UTC before hashing, so a hash computed at insert time
    still matches on re-verify after Postgres round-trips the timestamp
    back as UTC regardless of the offset it was inserted with."""
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
