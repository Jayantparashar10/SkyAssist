"""Reply validator. Pure, no I/O, no LLM.

Checks every rupee amount, hour count, time of day, flight number, and
policy-sensitive word in a candidate reply against what the decisions
actually say, plus the customer's own booked flight numbers (the only
flight numbers that may ever legitimately appear — there is no inventory
table, so nothing else exists to name). This is what stops the reply-
writing LLM from adding an offer, amount, date or flight it wasn't given.
On failure, the caller regenerates once, then falls back to templates.py.
"""

from __future__ import annotations

import re

from schemas import Decision

SENSITIVE_WORDS = [
    "upgrade",
    "business class",
    "business-class",
    "full night",
    "full-night",
    "waive",
    "waived",
    "waiver",
    "compensation",
]

_AMOUNT_RE = re.compile(r"₹[\d,]+")
_HOURS_RE = re.compile(r"\d+(?:\.\d+)?h\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_FLIGHT_RE = re.compile(r"\b[A-Za-z]{2}-\d{2,4}\b")

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_WEEKDAYS = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*"
_DATE_RE = re.compile(
    rf"\b(?:{_WEEKDAYS}\s+\d{{1,2}}\s+{_MONTHS}|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}|{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)


def _decided_facts_text(decisions: list[Decision]) -> str:
    return " ".join(fact for d in decisions for fact in d.customer_facing_facts).lower()


def validate_reply(reply: str, decisions: list[Decision], own_flight_numbers: set[str] = frozenset()) -> bool:
    reply_lower = reply.lower()
    decided_text = _decided_facts_text(decisions)

    decided_amounts = set(_AMOUNT_RE.findall(decided_text))
    for amount in _AMOUNT_RE.findall(reply):
        if amount not in decided_amounts:
            return False

    decided_hours = set(_HOURS_RE.findall(decided_text))
    for hours in _HOURS_RE.findall(reply_lower):
        if hours not in decided_hours:
            return False

    decided_times = set(_TIME_RE.findall(decided_text))
    for time_token in _TIME_RE.findall(reply):
        if time_token not in decided_times:
            return False

    allowed_flights = {f.upper() for f in own_flight_numbers if f}
    for flight in _FLIGHT_RE.findall(reply):
        if flight.upper() not in allowed_flights:
            return False

    decided_dates = {m.lower() for m in _DATE_RE.findall(decided_text)}
    for date_token in _DATE_RE.findall(reply):
        if date_token.lower() not in decided_dates:
            return False

    for word in SENSITIVE_WORDS:
        if word in reply_lower and word not in decided_text:
            return False

    return True
