"""Seed data transcribed verbatim from the data pack (``Assignment 3
Customer Resolution Agent.pdf``) plus the one fare quote the data pack
itself gives (Scenario 3 — a ₹2,000 fare difference, no flight number,
time or route, so none is invented). This is the single source of
customer/booking/fare-quote facts for both the real Postgres seed
(``python -m seed``, run from inside ``backend/``, and the ``/api/reset``
handler) and the test fixtures in ``backend/tests/`` — one source, so a
test can never silently drift from what actually gets loaded into the
database.

The only field here that isn't verbatim from the data pack is
``origin``/``destination``, which use the city names as given rather than
an invented IATA code.
"""

from __future__ import annotations

from schemas import Booking, Customer, FareQuote

CUSTOMERS = [
    {
        "name": "Priya Nair",
        "last_name": "Nair",
        "tier": "Gold",
        "email": "priya.nair@example.com",
        "phone": "+91-98xxxxxxx1",
        "history": "6 flights, 1 prior complaint (delayed baggage, resolved with voucher)",
    },
    {
        "name": "Arvind Kulkarni",
        "last_name": "Kulkarni",
        "tier": "Silver",
        "email": "arvind.kulkarni@example.com",
        "phone": "+91-98xxxxxxx2",
        "history": "3 flights, no prior complaints",
    },
    {
        "name": "Meher Kaur",
        "last_name": "Kaur",
        "tier": "Platinum",
        "email": "meher.kaur@example.com",
        "phone": "+91-98xxxxxxx3",
        "history": "10 flights, 1 prior complaint (overbooking, resolved with a tier-status upgrade)",
    },
]

# customer_name resolves to a customer_id when seeded (see build_fixtures / db seeding).
BOOKINGS = [
    {
        "customer_name": "Priya Nair", "pnr": "SK4821X", "leg": 1, "flight_no": "SK-204",
        "origin": "Delhi", "destination": "Goa", "sched_dep": "2026-09-23T18:40:00+05:30",
        "status": "cancelled", "new_dep": None, "cause": "operational reasons",
    },
    {
        "customer_name": "Priya Nair", "pnr": "SK4821X", "leg": 2, "flight_no": None,
        "origin": "Goa", "destination": "Delhi", "sched_dep": "2026-09-25T16:20:00+05:30",
        "status": "scheduled", "new_dep": None, "cause": None,
    },
    {
        "customer_name": "Arvind Kulkarni", "pnr": "TR1190B", "leg": 1, "flight_no": "SK-118",
        "origin": "Mumbai", "destination": "Bengaluru", "sched_dep": "2026-09-23T07:10:00+05:30",
        "status": "delayed", "new_dep": "2026-09-23T11:10:00+05:30", "cause": None,
    },
    {
        "customer_name": "Meher Kaur", "pnr": "WL7742", "leg": 1, "flight_no": "SK-305",
        "origin": "Delhi", "destination": "Hyderabad", "sched_dep": "2026-09-23T14:00:00+05:30",
        "status": "delayed", "new_dep": "2026-09-23T20:00:00+05:30", "cause": None,
    },
]

FARE_QUOTES = [
    {
        "pnr": "WL7742",
        "description": "Alternative higher-fare flight Meher asked about instead of waiting out the delay",
        "fare_diff_inr": 2000,
        "source": "Data pack, Scenario 3",
    },
]


def build_fixtures() -> tuple[dict[str, Customer], dict[str, list[Booking]], dict[str, list[FareQuote]]]:
    """Pure, in-memory typed fixtures — no DB. Used by tests and by the
    in-memory store. Returns (customers_by_pnr, bookings_by_pnr, fare_quotes_by_pnr).
    """
    customers_by_name: dict[str, Customer] = {
        c["name"]: Customer(id=i + 1, **c) for i, c in enumerate(CUSTOMERS)
    }
    bookings_by_pnr: dict[str, list[Booking]] = {}
    customers_by_pnr: dict[str, Customer] = {}
    for i, b in enumerate(BOOKINGS):
        customer = customers_by_name[b["customer_name"]]
        booking = Booking(
            id=i + 1,
            pnr=b["pnr"],
            customer_id=customer.id,
            leg=b["leg"],
            flight_no=b["flight_no"],
            origin=b["origin"],
            destination=b["destination"],
            sched_dep=b["sched_dep"],
            status=b["status"],
            new_dep=b["new_dep"],
            cause=b["cause"],
        )
        bookings_by_pnr.setdefault(b["pnr"], []).append(booking)
        customers_by_pnr[b["pnr"]] = customer

    fare_quotes_by_pnr: dict[str, list[FareQuote]] = {}
    for i, q in enumerate(FARE_QUOTES):
        fare_quotes_by_pnr.setdefault(q["pnr"], []).append(FareQuote(id=i + 1, **q))

    return customers_by_pnr, bookings_by_pnr, fare_quotes_by_pnr


if __name__ == "__main__":
    # Run from inside backend/: `python -m seed` — seeds a fresh Neon database.
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env.local")

    from db import PostgresStore, get_connection

    with get_connection() as conn:
        PostgresStore(conn).reset_and_seed()
    print("Seeded customers, bookings and fare quotes.")
