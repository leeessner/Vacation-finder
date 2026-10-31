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
comes back empty, this works down `fallback_ladder`: two adults scaled to the
party, one adult scaled, then the same with one more stop allowed. Checked
against Google's own family-of-six price that day, the two-adult estimate ran
3-8% high — the safe direction for a budget. Every fare records which rung
produced it (`source`), and the reports say when it's an estimate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from ..config import Settings, Trip
from ..flight_details import from_fast_flights, total_minutes
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
    return_from: str = "",
) -> FlightQuote | None:
    """Cheapest Google Flights round-trip result, or None if the scrape failed.

    With `return_from`, the flight home leaves from that airport instead: a
    multi-city (open-jaw) search, priced as one ticket.
    """
    if not available():
        log.debug("fast-flights unavailable: %s", import_error())
        return None

    party = trip.party
    found, source = None, "google_flights"
    for step in fallback_ladder(trip):
        hit = _cheapest(
            trip, origin, destination, depart, ret, currency,
            adults=step.adults, extra_stops=step.extra_stops, return_from=return_from,
            max_hours=settings.max_flight_hours,
        )
        if hit is not None:
            price = hit[0] if step.adults is None else hit[0] / step.adults * party.total
            found, source = (price, hit[1], hit[2]), step.source
            break
    if found is None:
        return None

    best_price, best_airlines, outbound = found
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
        stops_out=max(0, len(outbound) - 1) if outbound else None,
        stops_back=None,
        source=source,
        outbound=outbound,
        deep_link=(
            "https://www.google.com/travel/flights?q="
            f"Flights%20to%20{destination}%20from%20{origin}%20on%20"
            f"{depart.isoformat()}%20through%20{ret.isoformat()}"
            + (f"%20returning%20from%20{return_from}" if return_from else "")
        ),
    )


@dataclass(frozen=True)
class Step:
    """One rung of the fallback ladder.

    `adults` None means the whole party at its real composition; a number
    means that many adults, scaled to the party. `extra_stops` loosens the
    trip's stop limit by that many.
    """

    adults: int | None
    extra_stops: int
    source: str


def fallback_ladder(trip: Trip) -> list[Step]:
    """Searches to try in order, stopping at the first that returns a fare.

    Ordered by how faithfully each prices the real trip. Checked live in
    October 2026 (STL to Europe, family of six, at most one stop): the whole
    party returned nothing to Barcelona or Paris, two adults did; for one
    July Barcelona date only one adult did; Munich only appeared once a
    second stop was allowed. Nice returned nothing at all — that trip's
    airfare comes from the weekly SerpApi check instead.
    """
    steps = [Step(None, 0, "google_flights")]
    if trip.party.total > 2:
        steps.append(Step(2, 0, "google_flights_scaled"))
    if trip.party.total > 1:
        steps.append(Step(1, 0, "google_flights_scaled"))
    if trip.flights.max_stops is not None:
        steps.append(Step(None, 1, "google_flights_extra_stop"))
        if trip.party.total > 2:
            steps.append(Step(2, 1, "google_flights_scaled_extra_stop"))
    return steps


def _cheapest(
    trip: Trip,
    origin: str,
    destination: str,
    depart: date,
    ret: date,
    currency: str,
    adults: int | None = None,
    extra_stops: int = 0,
    return_from: str = "",
    trip_type: str = "",
    max_hours: float | None = None,
) -> tuple[float, tuple[str, ...], tuple] | None:
    """(price, airlines, outbound segments) of the cheapest result, or None."""
    who = "party" if adults is None else f"{adults} adult(s)"
    try:
        query = build_query(
            trip, origin, destination, depart, ret, currency,
            adults=adults, extra_stops=extra_stops, return_from=return_from,
            trip_type=trip_type,
        )
        results = get_flights(query)
    except Exception as exc:  # noqa: BLE001 - scraping fails in many shapes
        # An empty results page makes fast-flights raise TypeError rather than
        # return nothing; either way there is no fare here.
        log.info(
            "Google Flights %s +%d stop(s) %s->%s %s: no results (%s)",
            who, extra_stops, origin, destination, depart, exc,
        )
        return None

    best: tuple[float, tuple[str, ...], tuple] | None = None
    for item in results or []:
        price = _price_of(item)
        if price is None or price <= 0:
            continue
        segments = from_fast_flights(item)
        if too_long(segments, max_hours):
            continue
        if best is None or price < best[0]:
            best = (price, tuple(getattr(item, "airlines", ()) or ()), segments)
    return best


def too_long(segments: tuple, max_hours: float | None) -> bool:
    """Over the gate-to-gate limit. A flight whose time can't be worked out
    is kept rather than dropped."""
    if not max_hours or not segments:
        return False
    minutes = total_minutes(segments)
    return minutes is not None and minutes > max_hours * 60


def typical_leg(
    trip: Trip, origin: str, destination: str, day: date, currency: str,
    max_hours: float | None = None,
) -> tuple[tuple, tuple[str, ...]] | None:
    """(segments, airlines) of the cheapest one-way flight on this route and
    day, for one adult: what the trip home typically looks like. Free (the
    scraper), and never used as a price."""
    if not available():
        return None
    hit = _cheapest(
        trip, origin, destination, day, day, currency, adults=1, trip_type="one-way",
        max_hours=max_hours,
    )
    return (hit[2], hit[1]) if hit else None


def build_query(
    trip: Trip,
    origin: str,
    destination: str,
    depart: date,
    ret: date,
    currency: str,
    adults: int | None = None,
    extra_stops: int = 0,
    return_from: str = "",
    trip_type: str = "",
):
    """The fast-flights query for one round trip: the whole party, or with
    `adults`, that many adults and no bag filter (a stand-in to scale).
    With `return_from`, a multi-city trip home from that airport; with
    trip_type "one-way", only the first flight."""
    party = trip.party
    sample = adults is not None
    stops = trip.flights.max_stops
    if stops is not None:
        stops += extra_stops
    if sample:
        passengers = Passengers(adults=adults)
    else:
        passengers = Passengers(
            adults=party.adult_equivalents,
            children=party.child_seats,
            infants_in_seat=0,
            infants_on_lap=party.infants,
        )
    legs = [
        FlightQuery(
            date=depart.isoformat(),
            from_airport=origin,
            to_airport=destination,
            max_stops=stops,
        ),
        FlightQuery(
            date=ret.isoformat(),
            from_airport=return_from or destination,
            to_airport=origin,
            max_stops=stops,
        ),
    ]
    if trip_type == "one-way":
        legs, kind = legs[:1], "one-way"
    else:
        kind = "multi-city" if return_from and return_from != destination else "round-trip"
    return create_query(
        flights=legs,
        trip=kind,
        seat=trip.flights.cabin,
        passengers=passengers,
        currency=currency,
        max_stops=stops,
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
