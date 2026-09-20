"""Guards. Pure, no I/O, no LLM calls — a deterministic backstop layered
after understand.py, before policy.py sees the Understanding.

Every check here can only make an Understanding *more* cautious (legal/
injection flags never get cleared once set). Guards never drops or
reclassifies an intent by pattern-matching — that's a language-
understanding judgment that belongs in understand.py's prompt.
"""

from __future__ import annotations

import re

from schemas import Understanding

LEGAL_KEYWORDS = [
    "legal action",
    "take legal",
    "lawyer",
    "advocate",
    "sue you",
    "suing",
    "in court",
    "consumer forum",
    "consumer court",
    "legal notice",
    "formal complaint",
    "file a complaint",
    "filing a complaint",
    "vakil",
    "adalat",
    "case daalunga",
    "case karunga",
    "shikayat darj",
    "consumer commission",
]

INJECTION_PATTERNS = [
    r"ignore\b.{0,25}\b(instructions|rules|prompt|polic\w*)",
    r"disregard\b.{0,25}\b(instructions|rules|prompt|polic\w*)",
    r"forget\b.{0,25}\b(instructions|rules|prompt|polic\w*)",
    r"you are now",
    r"act as (if|though) you",
    r"system prompt",
    r"new instructions",
    r"the counter staff (promised|said|told)",
]


def check_legal_or_complaint(message: str) -> bool:
    lowered = message.lower()
    return any(kw in lowered for kw in LEGAL_KEYWORDS)


def check_injection(message: str) -> bool:
    lowered = message.lower()
    return any(re.search(pattern, lowered) for pattern in INJECTION_PATTERNS)


def apply_guards(message: str, understanding: Understanding) -> Understanding:
    """Returns a new Understanding with the keyword/pattern backstop
    applied on top of whatever the LLM already produced."""
    data = understanding.model_dump()
    data["legal_threat"] = understanding.legal_threat or check_legal_or_complaint(message)
    data["injection_attempt"] = understanding.injection_attempt or check_injection(message)
    return Understanding(**data)


def is_privacy_violation(other_pnr_or_name: str | None, own_pnr: str, own_name: str) -> bool:
    """True when the customer is asking about a booking/PNR/name that is
    not their own logged-in identity."""
    if not other_pnr_or_name:
        return False
    ref = other_pnr_or_name.strip().lower()
    if not ref:
        return False
    own_first_name = own_name.strip().lower().split()[0] if own_name.strip() else ""
    return ref != own_pnr.strip().lower() and ref not in own_name.strip().lower() and ref != own_first_name
