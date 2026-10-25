"""Google Flights via the `fast-flights` scraper — no API key, no quota.

This source is deliberately best-effort. Google changes its page without
notice and the library's API has shifted between majors, so every call site
tolerates failure: if this returns None the run carries on without that fare.

**Large parties on long-haul routes.** The scraper reads the version of the
results page Google renders first, and for six passengers on a transatlantic
route that page is empty — checked live in October 2026: STL-Barcelona and
STL-Paris returned nothing for six (adults or a family, with or without bags,
on retry), while two adults returned six itineraries. The full interactive
site, which SerpApi drives, does list them. So when the whole-party search
comes back empty, this prices two adults and scales to the party. Checked
against Google's own family-of-six price that day, the estimate ran 3-8% high
— the safe direction for a budget, and constant from day to day, so trends
are unaffected. Such fares are marked `google_flights_scaled` and noted.
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
    found = _cheapest(trip, origin, destination, depart, ret, currency)
    source = "google_flights"
    if found is None and party.total > SCALE_FROM:
        # Google's first-rendered page lists nothing for big parties on many
        # long-haul routes (see the module docstring); price two adults.
        found = _cheapest(trip, origin, destination, depart, ret, currency, sample=True)
        if found is not None:
            per_person = found[0] / SCALE_FROM
            found = (per_person * party.total, found[1])
            source = "google_flights_scaled"
    if found is None:
        return None

    best_price, best_airlines = found
    if source == "google_flights" and not settings.google_flights_price_is_total:
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
        source=source,
        deep_link=(
            "https://www.google.com/travel/flights?q="
            f"Flights%20to%20{destination}%20from%20{origin}%20on%20"
            f"{depart.isoformat()}%20through%20{ret.isoformat()}"
        ),
    )


# Adults priced when the whole party can't be: the smallest search that
# reliably returns long-haul results, and still a real two-seat booking.
SCALE_FROM = 2


def _cheapest(
    trip: Trip,
    origin: str,
    destination: str,
    depart: date,
    ret: date,
    currency: str,
    sample: bool = False,
) -> tuple[float, tuple[str, ...]] | None:
    """(price, airlines) of the cheapest result, or None if there were none."""
    who = "2 adults" if sample else "party"
    try:
        query = build_query(trip, origin, destination, depart, ret, currency, sample=sample)
        results = get_flights(query)
    except Exception as exc:  # noqa: BLE001 - scraping fails in many shapes
        # An empty results page makes fast-flights raise TypeError rather than
        # return nothing; either way there is no fare here.
        log.info("Google Flights %s %s->%s %s: no results (%s)", who, origin, destination, depart, exc)
        return None

    best: tuple[float, tuple[str, ...]] | None = None
    for item in results or []:
        price = _price_of(item)
        if price is None or price <= 0:
            continue
        if best is None or price < best[0]:
            best = (price, tuple(getattr(item, "airlines", ()) or ()))
    return best


def build_query(
    trip: Trip,
    origin: str,
    destination: str,
    depart: date,
    ret: date,
    currency: str,
    sample: bool = False,
):
    """The fast-flights query for one round trip: the whole party, or with
    `sample`, two adults and no bag filter (a two-seat stand-in)."""
    party = trip.party
    if sample:
        passengers = Passengers(adults=SCALE_FROM)
    else:
        passengers = Passengers(
            adults=party.adult_equivalents,
            children=party.child_seats,
            infants_in_seat=0,
            infants_on_lap=party.infants,
        )
    return create_query(
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
        passengers=passengers,
        currency=currency,
        max_stops=trip.flights.max_stops,
        checked_bags=0 if sample else trip.flights.checked_bags,
        carry_on_bags=0 if sample else trip.flights.carry_on_bags,
        exclude_basic_economy=trip.flights.exclude_basic_economy,
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
