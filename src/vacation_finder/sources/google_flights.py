"""Google Flights via the `fast-flights` scraper — no API key, no quota.

This source is deliberately best-effort. Google changes its page without
notice and the library's API has shifted between majors, so every call site
tolerates failure: if this returns None the tracker carries on with Amadeus
and the digest notes that the cross-check was unavailable.
"""

from __future__ import annotations

import logging
from datetime import date

from ..config import Settings, Trip
from ..models import FlightQuote

log = logging.getLogger(__name__)

_IMPORT_ERROR: Exception | None = None

try:  # pragma: no cover - exercised only by the import environment
    from fast_flights import FlightQuery, Passengers, create_query, get_flights
except Exception as exc:  # noqa: BLE001 - any import failure degrades the same way
    FlightQuery = Passengers = create_query = get_flights = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


def available() -> bool:
    return _IMPORT_ERROR is None


def import_error() -> str:
    return "" if _IMPORT_ERROR is None else str(_IMPORT_ERROR)


def search_flights(
    trip: Trip,
    origin: str,
    destination: str,
    depart: date,
    ret: date,
    currency: str,
    settings: Settings,
) -> FlightQuote | None:
    """Cheapest Google Flights round-trip result, or None if the scrape failed."""
    if not available():
        log.debug("fast-flights unavailable: %s", import_error())
        return None

    party = trip.party
    try:
        query = create_query(
            flights=[
                FlightQuery(
                    date=depart.isoformat(),
                    from_airport=origin,
                    to_airport=destination,
                    max_stops=trip.flights.max_stops,
                ),
                FlightQuery(
                    date=ret.isoformat(),
                    from_airport=destination,
                    to_airport=origin,
                    max_stops=trip.flights.max_stops,
                ),
            ],
            trip="round-trip",
            seat=trip.flights.cabin,
            passengers=Passengers(
                adults=party.adult_equivalents,
                children=party.child_seats,
                infants_in_seat=0,
                infants_on_lap=party.infants,
            ),
            currency=currency,
            max_stops=trip.flights.max_stops,
            checked_bags=trip.flights.checked_bags,
            carry_on_bags=trip.flights.carry_on_bags,
            exclude_basic_economy=trip.flights.exclude_basic_economy,
        )
        results = get_flights(query)
    except Exception as exc:  # noqa: BLE001 - scraping fails in many shapes
        log.warning("Google Flights scrape failed %s->%s %s: %s", origin, destination, depart, exc)
        return None

    best_price: float | None = None
    best_airlines: tuple[str, ...] = ()
    for item in results or []:
        price = _price_of(item)
        if price is None or price <= 0:
            continue
        if best_price is None or price < best_price:
            best_price = price
            best_airlines = tuple(getattr(item, "airlines", ()) or ())

    if best_price is None:
        return None

    if not settings.google_flights_price_is_total:
        best_price *= max(1, party.total)

    return FlightQuote(
        origin=origin,
        destination=destination,
        depart_date=depart,
        return_date=ret,
        total_price=float(best_price),
        currency=currency,
        carriers=best_airlines,
        stops_out=None,
        stops_back=None,
        source="google_flights",
        deep_link=(
            "https://www.google.com/travel/flights?q="
            f"Flights%20to%20{destination}%20from%20{origin}%20on%20"
            f"{depart.isoformat()}%20through%20{ret.isoformat()}"
        ),
    )


def _price_of(item: object) -> float | None:
    """Read a price off a result item, tolerating '$1,234' strings or ints."""
    raw = getattr(item, "price", None)
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    digits = "".join(ch for ch in str(raw) if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None
