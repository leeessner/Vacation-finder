"""Hotel and vacation-rental prices from Google Hotels, through SerpApi.

Google Hotels is the broadest lodging price source there is: it aggregates
Booking.com, Expedia, Hotels.com, the hotels' own sites and — unlike the
affiliate APIs this replaced — vacation rentals. It is also priced for the
actual guests: it takes adult counts and children's ages, not one adult.

Google publishes no hotel API, so this goes through SerpApi, which runs the
search and returns it as JSON. Its free plan is 250 searches a month, which is
the constraint everything in `lodging.py` is built around. Only successful
searches count; errors and empty results are free.

Two things worth knowing when reading the numbers:

- These are live prices for the dates asked, as Google shows them today —
  not a cache of other people's searches.
- A hotel stay for a big family is priced as N identical rooms at the same
  hotel (`lodging.rooms`), each holding its share of the party. A rental is
  one unit for everyone, so it is never multiplied.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date
from typing import Any

import requests

from ..config import Settings, Trip
from ..models import LodgingQuote, Party
from .base import RateLimiter, SourceError, SourceUnavailable, request_with_retry

log = logging.getLogger(__name__)

SEARCH_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"
SIGNUP_HINT = "get a free key at serpapi.com (250 searches/month, no card)"

SORT_LOWEST_PRICE = 3

# Google Hotels' review-score filter takes codes, not scores.
RATING_CODES = ((4.5, 9), (4.0, 8), (3.5, 7))


class SerpApiClient:
    """Minimal SerpApi client. The key travels as a query parameter, because
    that is the only way SerpApi accepts it, so every error message is
    scrubbed of it before it can reach a log or the committed history CSV."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("SERPAPI_KEY", "")
        self.session = requests.Session()
        self.limiter = RateLimiter(1.0)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise SourceUnavailable(f"SERPAPI_KEY is not set — {SIGNUP_HINT}")
        payload = self._get(SEARCH_URL, {**params, "engine": "google_hotels"})
        error = payload.get("error")
        if error:
            # "Google Hotels hasn't returned any results" arrives as an error
            # too; either way there is nothing to price and nothing was spent.
            raise SourceError(f"Google Hotels: {error}")
        return payload

    def searches_left(self) -> int | None:
        """Searches remaining this month, or None if it can't be told.

        The account endpoint is free — it does not use up a search.
        """
        if not self.configured:
            return None
        try:
            payload = self._get(ACCOUNT_URL, {})
        except SourceError as exc:
            log.info("could not read SerpApi account: %s", exc)
            return None
        for key in ("total_searches_left", "plan_searches_left"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return int(value)
        return None

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {k: v for k, v in params.items() if v not in (None, "")}
        query["api_key"] = self.api_key
        try:
            response = request_with_retry(
                self.session, "GET", url, limiter=self.limiter, params=query, timeout=60
            )
        except SourceError as exc:
            raise SourceError(self._scrub(str(exc))) from None

        if response.status_code == 401:
            raise SourceUnavailable("SerpApi rejected SERPAPI_KEY — check the secret")
        try:
            payload = response.json()
        except ValueError:
            raise SourceError(
                f"SerpApi returned {response.status_code} with non-JSON body"
            ) from None
        if response.status_code != 200:
            message = payload.get("error") if isinstance(payload, dict) else None
            raise SourceError(
                self._scrub(f"SerpApi {response.status_code}: {message or response.text[:200]}")
            )
        if not isinstance(payload, dict):
            raise SourceError("SerpApi returned an unexpected shape")
        return payload

    def _scrub(self, text: str) -> str:
        return text.replace(self.api_key, "***") if self.api_key else text


# --------------------------------------------------------------------------- #
# Who sleeps where
# --------------------------------------------------------------------------- #

def room_occupancy(party: Party, rooms: int) -> tuple[int, tuple[int, ...]]:
    """Guests in the fullest of `rooms` identical rooms.

    Six people in two rooms is three to a room. The oldest children go in the
    priced room, because a room's price can depend on the children's ages and
    the rest of the stay is assumed to cost the same.
    """
    rooms = max(1, rooms)
    adults = max(1, math.ceil(party.adults / rooms))
    ages = sorted(party.children, reverse=True)
    kids = tuple(ages[: math.ceil(len(ages) / rooms)])
    return adults, kids


def search_params(
    trip: Trip, query: str, check_in: date, check_out: date, currency: str
) -> dict[str, Any]:
    prefs = trip.lodging
    rental = prefs.kind == "rental"
    if rental:
        adults, kids = trip.party.adults, tuple(trip.party.children)
    else:
        adults, kids = room_occupancy(trip.party, prefs.rooms)

    params: dict[str, Any] = {
        "q": query,
        "check_in_date": check_in.isoformat(),
        "check_out_date": check_out.isoformat(),
        "adults": adults,
        "children": len(kids),
        "children_ages": ",".join(str(max(1, age)) for age in kids) or None,
        "currency": currency,
        "gl": "us",
        "hl": "en",
        "sort_by": SORT_LOWEST_PRICE,
    }
    if rental:
        params["vacation_rentals"] = "true"
        params["bedrooms"] = prefs.bedrooms or prefs.rooms
    elif prefs.min_stars:
        lowest = max(2, math.ceil(prefs.min_stars))
        params["hotel_class"] = ",".join(str(s) for s in range(lowest, 6))
    if prefs.min_rating:
        code = next((c for score, c in RATING_CODES if prefs.min_rating >= score), None)
        if code:
            params["rating"] = code
    return params


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #

def search_lodging(
    client: SerpApiClient,
    trip: Trip,
    query: str,
    check_in: date,
    check_out: date,
    currency: str,
    settings: Settings,
) -> LodgingQuote | None:
    """Cheapest property that meets the trip's lodging rules, for the whole party."""
    prefs = trip.lodging
    nights = (check_out - check_in).days or 1
    rental = prefs.kind == "rental"
    units = 1 if rental else max(1, prefs.rooms)

    payload = client.search(search_params(trip, query, check_in, check_out, currency))
    properties = payload.get("properties") or []
    if not isinstance(properties, list):
        return None

    best: LodgingQuote | None = None
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        kind = str(prop.get("type", "")).lower()
        if rental and kind and "rental" not in kind:
            continue
        if not rental and "rental" in kind:
            continue

        one_unit = stay_price(prop, nights)
        if one_unit is None:
            continue
        stars = _opt_float(prop.get("extracted_hotel_class"))
        if not rental and prefs.min_stars and (stars is None or stars < prefs.min_stars):
            continue
        rating = _opt_float(prop.get("overall_rating"))
        if prefs.min_rating and (rating is None or rating < prefs.min_rating):
            continue

        total = round(one_unit * units, 2)
        if prefs.max_per_night and total / nights > prefs.max_per_night:
            continue
        if best is not None and total >= best.total_price:
            continue

        name = str(prop.get("name") or "unknown")
        best = LodgingQuote(
            destination=query,
            check_in=check_in,
            check_out=check_out,
            property_name=name,
            total_price=total,
            currency=currency,
            property_id=str(prop.get("property_token") or ""),
            nights=nights,
            rating=rating,
            stars=stars,
            source="google_hotels",
            deep_link=google_hotels_link(name, query, check_in, check_out),
        )
    return best


def stay_price(prop: dict[str, Any], nights: int) -> float | None:
    """One room's (or one rental's) price for the whole stay.

    Google gives a stay total and a nightly rate; the total is preferred
    because it carries the fees a nightly rate leaves out.
    """
    total = _extracted(prop.get("total_rate"))
    if total and total > 0:
        return total
    nightly = _extracted(prop.get("rate_per_night"))
    if nightly and nightly > 0:
        return nightly * nights
    return None


def google_hotels_link(name: str, location: str, check_in: date, check_out: date) -> str:
    query = requests.utils.quote(f"{name} {location}".strip())
    return (
        f"https://www.google.com/travel/search?q={query}"
        f"&checkin={check_in.isoformat()}&checkout={check_out.isoformat()}"
    )


def _extracted(block: Any) -> float | None:
    if not isinstance(block, dict):
        return None
    return _opt_float(block.get("extracted_lowest"))


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
