"""The flights behind a fare: airports, connections, times, and how long it takes.

A fare on its own says what it costs, not what the day looks like. Both
price sources list the segments of the option they priced, with times
local to each airport:

- Google Flights (the free daily scraper) and SerpApi's weekly check both
  return the outbound flights of the cheapest option. On Google, a round
  trip or multi-city fare is listed by its outbound flights; the return is
  chosen afterwards, so neither source gives the priced return.
- For the way home, one free scraper search of the same route one-way shows
  a typical return (airports, connection, times). It is labelled as typical,
  not as the priced flight.

Details are kept per trip in data/state/flight_details.json for the trip's
latest best option, and the trip page draws them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

STATE_NAME = "flight_details"


# --------------------------------------------------------------------------- #
# Segments, from either source
# --------------------------------------------------------------------------- #

def segment(
    origin: str, origin_name: str, dest: str, dest_name: str,
    depart: datetime | None, arrive: datetime | None, minutes: int | None,
    airline: str = "", flight: str = "", plane: str = "",
) -> dict[str, Any]:
    return {
        "from": origin, "from_name": origin_name, "to": dest, "to_name": dest_name,
        "depart": depart.strftime("%Y-%m-%dT%H:%M") if depart else "",
        "arrive": arrive.strftime("%Y-%m-%dT%H:%M") if arrive else "",
        "minutes": int(minutes) if minutes else None,
        "airline": airline, "flight": flight, "plane": plane,
    }


def from_fast_flights(item: Any) -> tuple[dict[str, Any], ...]:
    """The segments of one fast-flights result (a `Flights`)."""
    out = []
    for leg in getattr(item, "flights", None) or []:
        try:
            dep, arr = leg.departure, leg.arrival
            depart = datetime(*dep.date, *dep.time) if dep.date else None
            arrive = datetime(*arr.date, *arr.time) if arr.date else None
        except (AttributeError, TypeError, ValueError):
            depart = arrive = None
        out.append(segment(
            getattr(leg.from_airport, "code", ""), getattr(leg.from_airport, "name", ""),
            getattr(leg.to_airport, "code", ""), getattr(leg.to_airport, "name", ""),
            depart, arrive, getattr(leg, "duration", None),
            plane=str(getattr(leg, "plane_type", "") or ""),
        ))
    return tuple(out)


def from_serpapi(option: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """The segments of one SerpApi Google Flights option."""
    out = []
    for leg in option.get("flights") or []:
        if not isinstance(leg, dict):
            continue
        dep, arr = leg.get("departure_airport") or {}, leg.get("arrival_airport") or {}
        out.append(segment(
            str(dep.get("id", "")), str(dep.get("name", "")),
            str(arr.get("id", "")), str(arr.get("name", "")),
            _parse(dep.get("time")), _parse(arr.get("time")), leg.get("duration"),
            airline=str(leg.get("airline", "")), flight=str(leg.get("flight_number", "")),
            plane=str(leg.get("airplane", "")),
        ))
    return tuple(out)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Reading a leg
# --------------------------------------------------------------------------- #

def layovers(segments: tuple | list) -> list[tuple[str, int | None]]:
    """(airport, minutes) between consecutive segments. Both times are local
    to the same airport, so the difference is real."""
    out = []
    for prev, nxt in zip(segments, segments[1:]):
        a, b = _parse(prev.get("arrive")), _parse(nxt.get("depart"))
        out.append((prev.get("to", ""), int((b - a).total_seconds() // 60) if a and b else None))
    return out


def total_minutes(segments: tuple | list) -> int | None:
    """Flying plus connections, gate to gate. Not arrival minus departure:
    those are in different time zones."""
    if not segments:
        return None
    flying = [s.get("minutes") for s in segments]
    waits = [m for _, m in layovers(segments)]
    if any(m is None for m in flying + waits):
        return None
    return sum(flying) + sum(waits)


def hours(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def day_shift(segments: tuple | list) -> int:
    """How many calendar days later (local time) the leg lands than it leaves."""
    first, last = _parse(segments[0].get("depart")), _parse(segments[-1].get("arrive"))
    if not (first and last):
        return 0
    return (last.date() - first.date()).days


# --------------------------------------------------------------------------- #
# How the fare was arrived at
# --------------------------------------------------------------------------- #

def basis(source: str, party_total: int, max_stops: int | None, bags: int,
          insight: Any = None, searched_on: date | None = None) -> str:
    """One plain paragraph saying where the airfare number came from."""
    stops = "nonstop" if max_stops == 0 else f"at most {max_stops} stop(s) each way" if max_stops is not None else "any number of stops"
    when = f" on {searched_on:%b %d}" if searched_on else ""
    if source == "google_flights":
        return (f"The cheapest fare Google Flights listed{when} for all {party_total} travellers, "
                f"economy, {stops}, with {bags} checked bag(s) included.")
    if source.startswith("google_flights_scaled"):
        extra = " One more stop than your limit was allowed, because none was found within it." if "extra_stop" in source else ""
        return (f"An estimate. Google Flights' first page{when} showed no fares for {party_total} "
                f"travellers, which is common for big parties on long-haul routes. So the tracker "
                f"priced a smaller party ({stops}) and multiplied that per-person fare by {party_total}. "
                f"Children's economy fares to Europe are usually close to adult fares, so this is a "
                f"fair guide, but it doesn't include checked bags.{extra}")
    if source == "google_flights_extra_stop":
        return (f"The cheapest fare for all {party_total} travellers{when} once one more stop than your "
                f"limit was allowed; none was found within it.")
    if source == "google_flights_weekly":
        typical = ""
        if insight is not None and getattr(insight, "typical_low", None) and getattr(insight, "typical_high", None):
            typical = (f" Google rated it {insight.level or 'unrated'} against a typical range of "
                       f"${insight.typical_low:,.0f}–${insight.typical_high:,.0f}.")
        return (f"Google Flights' lowest price for all {party_total} travellers for these dates, "
                f"from the weekly check (one paid search a week){when}, because the free daily "
                f"check found no fares for this route.{typical}")
    if source == "travelpayouts":
        return "A cached fare from Travelpayouts, scaled to the party."
    return "Source not recorded."
