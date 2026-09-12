"""Travelpayouts + Hotellook client — the replacement for Amadeus Self-Service.

Two things to understand about this data, because they change how you read
every number the tracker produces:

1. **It is cached, not live.** These endpoints return the cheapest fares and
   room rates *other people's searches* turned up recently, not a live
   availability check. For spotting a trend — which is what this project is
   for — that is fine and arguably steadier. For booking, it is a pointer,
   not a quote. Each fare carries a `found_at` we surface as staleness.

2. **Flight prices are per adult.** The flight endpoints take no passenger
   counts at all; they quote one adult. We scale by party size, which
   overstates slightly because children usually fly for less. Overstating is
   the safer direction for a budget tracker, but it is an estimate and the
   reports say so.

Auth is an affiliate token in the `X-Access-Token` header for the flight API,
and a `token` query parameter for Hotellook. They are the same token.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any

import requests

from ..config import Settings, Trip
from ..models import FlightQuote, LodgingQuote
from .base import RateLimiter, SourceError, SourceUnavailable, request_with_retry

log = logging.getLogger(__name__)

FLIGHTS_HOST = "https://api.travelpayouts.com"
HOTELS_HOST = "https://engine.hotellook.com/api/v2"

TRIP_CLASS = {"economy": 0, "premium-economy": 0, "business": 1, "first": 2}


class TravelpayoutsClient:
    """Thin REST client over the free affiliate endpoints."""

    def __init__(self, token: str | None = None, marker: str | None = None) -> None:
        self.token = token or os.environ.get("TRAVELPAYOUTS_TOKEN", "")
        self.marker = marker or os.environ.get("TRAVELPAYOUTS_MARKER", "")
        self.session = requests.Session()
        self.limiter = RateLimiter(0.25)

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _require_token(self) -> None:
        if not self.configured:
            raise SourceUnavailable(
                "TRAVELPAYOUTS_TOKEN is not set — get one free at "
                "travelpayouts.com (Tools -> API)"
            )

    def get_flights(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self._require_token()
        response = request_with_retry(
            self.session,
            "GET",
            f"{FLIGHTS_HOST}{path}",
            limiter=self.limiter,
            params={k: v for k, v in params.items() if v not in (None, "")},
            headers={"X-Access-Token": self.token, "Accept": "application/json"},
        )
        return self._unwrap(response, path)

    def get_hotels(self, path: str, params: dict[str, Any]) -> Any:
        self._require_token()
        merged = {k: v for k, v in params.items() if v not in (None, "")}
        merged["token"] = self.token
        response = request_with_retry(
            self.session,
            "GET",
            f"{HOTELS_HOST}{path}",
            limiter=self.limiter,
            params=merged,
            headers={"Accept": "application/json"},
        )
        return self._unwrap(response, path)

    @staticmethod
    def _unwrap(response: requests.Response, path: str) -> Any:
        if response.status_code == 401:
            raise SourceUnavailable(f"Travelpayouts rejected the token on {path}")
        if response.status_code != 200:
            raise SourceError(
                f"Travelpayouts {path} -> {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(f"Travelpayouts {path} returned non-JSON") from exc

        # The flight API wraps results in {success, data}; Hotellook returns a
        # bare list. Normalise only the error case, and let callers read shape.
        if isinstance(payload, dict) and payload.get("success") is False:
            raise SourceError(f"Travelpayouts {path}: {payload.get('error', payload)}")
        return payload


# --------------------------------------------------------------------------- #
# Flights
# --------------------------------------------------------------------------- #

def search_flights(
    client: TravelpayoutsClient,
    trip: Trip,
    origin: str,
    destination: str,
    depart: date,
    ret: date,
    currency: str,
    settings: Settings,
) -> FlightQuote | None:
    """Cheapest cached round-trip fare near these dates, scaled to the party."""
    payload = client.get_flights(
        "/v2/prices/week-matrix",
        {
            "origin": origin,
            "destination": destination,
            "depart_date": depart.isoformat(),
            "return_date": ret.isoformat(),
            "currency": currency.lower(),
            "trip_class": TRIP_CLASS.get(trip.flights.cabin, 0),
            "show_to_affiliates": "true",
        },
    )
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    best = _cheapest_row(rows, trip, depart, ret)
    if best is None:
        return None

    per_adult = float(best.get("value") or 0)
    if per_adult <= 0:
        return None

    travellers = max(1, trip.party.total)
    stops = best.get("number_of_changes")
    found_at = _parse_found_at(best.get("found_at"))

    notes = []
    if found_at:
        age_days = (datetime.utcnow() - found_at).days
        if age_days >= 3:
            notes.append(f"fare last seen {age_days}d ago")

    return FlightQuote(
        origin=origin,
        destination=destination,
        depart_date=_parse_date(best.get("depart_date")) or depart,
        return_date=_parse_date(best.get("return_date")) or ret,
        total_price=round(per_adult * travellers, 2),
        currency=currency,
        carriers=tuple(filter(None, [best.get("gate")])),
        stops_out=int(stops) if stops is not None else None,
        stops_back=int(stops) if stops is not None else None,
        source="travelpayouts",
        deep_link=google_flights_link(origin, destination, depart, ret),
    )


def _cheapest_row(
    rows: list[dict[str, Any]], trip: Trip, depart: date, ret: date
) -> dict[str, Any] | None:
    """Cheapest row that respects the trip's stop limit and matches the dates.

    week-matrix returns *nearby* dates as well as the exact ones. Taking the
    global cheapest would quietly price a different trip, so exact matches win
    and near dates are only a fallback.
    """
    eligible = []
    for row in rows:
        value = float(row.get("value") or 0)
        if value <= 0:
            continue
        stops = row.get("number_of_changes")
        if (
            trip.flights.max_stops is not None
            and stops is not None
            and int(stops) > trip.flights.max_stops
        ):
            continue
        eligible.append(row)

    if not eligible:
        return None

    exact = [
        r
        for r in eligible
        if _parse_date(r.get("depart_date")) == depart
        and _parse_date(r.get("return_date")) == ret
    ]
    return min(exact or eligible, key=lambda r: float(r["value"]))


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _parse_found_at(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "").split(".")[0])
    except (TypeError, ValueError):
        return None


def google_flights_link(origin: str, destination: str, depart: date, ret: date) -> str:
    """A clickable search, so an alert is one tap from checking it for real."""
    return (
        "https://www.google.com/travel/flights?q="
        f"Flights%20to%20{destination}%20from%20{origin}%20on%20"
        f"{depart.isoformat()}%20through%20{ret.isoformat()}"
    )


# --------------------------------------------------------------------------- #
# Deal radar — "what is unusually cheap from here right now?"
# --------------------------------------------------------------------------- #

def cheapest_destinations(
    client: TravelpayoutsClient,
    origin: str,
    currency: str,
    *,
    beginning_of_period: date | None = None,
    period_type: str = "month",
    limit: int = 100,
    trip_duration: int | None = None,
    one_way: bool = False,
) -> list[dict[str, Any]]:
    """The cheapest fares found out of `origin` recently, across all destinations.

    This is the inverse of the tracked-trip query: instead of asking what one
    route costs, it asks what the market is currently giving away from your
    home airport. It's the raw material for suggesting somewhere you hadn't
    thought to track, and it costs one call regardless of how many
    destinations come back.

    Prices are per adult, like every other flight figure from this API.
    """
    params: dict[str, Any] = {
        "origin": origin,
        "currency": currency.lower(),
        "period_type": period_type,
        "one_way": "true" if one_way else "false",
        "limit": limit,
        "page": 1,
        "sorting": "price",
        "show_to_affiliates": "true",
        "trip_class": 0,
    }
    if period_type == "month":
        params["beginning_of_period"] = (
            beginning_of_period or date.today().replace(day=1)
        ).isoformat()
    if trip_duration:
        params["trip_duration"] = trip_duration

    payload = client.get_flights("/v2/prices/latest", params)
    rows = payload.get("data", []) if isinstance(payload, dict) else []

    out: list[dict[str, Any]] = []
    for row in rows:
        value = _opt_float(row.get("value"))
        if not value or value <= 0:
            continue
        out.append(
            {
                "origin": row.get("origin", origin),
                "destination": row.get("destination", ""),
                "price_per_adult": value,
                "depart_date": _parse_date(row.get("depart_date")),
                "return_date": _parse_date(row.get("return_date")),
                "stops": row.get("number_of_changes"),
                "found_at": _parse_found_at(row.get("found_at")),
                "distance": row.get("distance"),
            }
        )
    out.sort(key=lambda r: r["price_per_adult"])
    return out


# --------------------------------------------------------------------------- #
# Hotels (Hotellook)
# --------------------------------------------------------------------------- #

def search_lodging(
    client: TravelpayoutsClient,
    trip: Trip,
    location: str,
    check_in: date,
    check_out: date,
    currency: str,
    settings: Settings,
) -> LodgingQuote | None:
    """Cheapest cached hotel for the stay, scaled to the number of rooms."""
    prefs = trip.lodging
    nights = (check_out - check_in).days or 1

    rows = client.get_hotels(
        "/cache.json",
        {
            "location": location,
            "checkIn": check_in.isoformat(),
            "checkOut": check_out.isoformat(),
            "currency": currency.lower(),
            "limit": settings.max_hotels_per_query,
        },
    )
    if not isinstance(rows, list) or not rows:
        return None

    best: LodgingQuote | None = None
    for row in rows:
        one_room = _room_price(row, nights, settings)
        if one_room is None or one_room <= 0:
            continue

        stars = _opt_float(row.get("stars"))
        if prefs.min_stars and stars is not None and stars < prefs.min_stars:
            continue

        total = one_room * max(1, prefs.rooms)
        per_night = total / nights
        if prefs.max_per_night and per_night > prefs.max_per_night:
            continue
        if best is not None and total >= best.total_price:
            continue

        best = LodgingQuote(
            destination=location,
            check_in=check_in,
            check_out=check_out,
            property_name=str(row.get("hotelName") or row.get("hotelId") or "unknown"),
            total_price=round(total, 2),
            currency=currency,
            property_id=str(row.get("hotelId") or ""),
            nights=nights,
            rating=_opt_float(row.get("rating")),
            stars=stars,
            source="travelpayouts",
            deep_link=google_hotels_link(
                str(row.get("hotelName") or ""), location, check_in, check_out
            ),
        )
    return best


def _room_price(row: dict[str, Any], nights: int, settings: Settings) -> float | None:
    """Hotellook's price for one room.

    Whether `priceAvg` covers the whole stay or a single night is the one
    thing about this API worth verifying against a real booking — so it's a
    setting rather than an assumption buried in code. `doctor` prints both
    readings so you can tell which is right in about a minute.
    """
    raw = row.get("priceFrom") or row.get("priceAvg")
    value = _opt_float(raw)
    if value is None:
        return None
    return value * nights if settings.hotel_price_is_per_night else value


def google_hotels_link(name: str, location: str, check_in: date, check_out: date) -> str:
    query = requests.utils.quote(f"{name} {location}".strip())
    return (
        f"https://www.google.com/travel/search?q={query}"
        f"&checkin={check_in.isoformat()}&checkout={check_out.isoformat()}"
    )


def lookup_location(client: TravelpayoutsClient, query: str) -> dict[str, Any] | None:
    """Resolve a city name to Hotellook's own id, for diagnostics."""
    payload = client.get_hotels(
        "/lookup.json", {"query": query, "lang": "en", "lookFor": "city", "limit": 1}
    )
    cities = (payload or {}).get("results", {}).get("locations", [])
    return cities[0] if cities else None


def _opt_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
