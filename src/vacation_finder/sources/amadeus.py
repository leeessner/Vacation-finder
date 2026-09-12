"""Amadeus Self-Service API client (free tier: no card, ~2k calls/month).

Two hosts exist. `test` is what a new account gets by default and returns
cached/synthetic data — fine for wiring things up, wrong for real decisions.
`production` returns live prices and is still free up to the monthly quota;
you switch by moving the app to production in the Amadeus dashboard.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any

import requests

from ..config import LodgingPrefs, Settings, Trip
from ..models import FlightQuote, LodgingQuote, Party
from .base import RateLimiter, SourceError, SourceUnavailable, request_with_retry

log = logging.getLogger(__name__)

HOSTS = {
    "test": "https://test.api.amadeus.com",
    "production": "https://api.amadeus.com",
}

CABIN_MAP = {
    "economy": "ECONOMY",
    "premium-economy": "PREMIUM_ECONOMY",
    "business": "BUSINESS",
    "first": "FIRST",
}

# Amadeus rejects very long hotelIds lists; 20 is comfortably under the limit.
HOTEL_BATCH = 20


class AmadeusClient:
    """Thin REST client. Deliberately not using the official SDK — one less dep."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        env: str = "test",
    ) -> None:
        self.client_id = client_id or os.environ.get("AMADEUS_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("AMADEUS_CLIENT_SECRET", "")
        self.host = HOSTS.get(env, HOSTS["test"])
        self.session = requests.Session()
        self.limiter = RateLimiter(0.15)
        self._token = ""
        self._token_expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _authenticate(self) -> str:
        if self._token and time.time() < self._token_expires_at - 30:
            return self._token
        if not self.configured:
            raise SourceUnavailable(
                "AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET are not set"
            )
        response = request_with_retry(
            self.session,
            "POST",
            f"{self.host}/v1/security/oauth2/token",
            limiter=self.limiter,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise SourceUnavailable(
                f"Amadeus auth failed ({response.status_code}): {response.text[:200]}"
            )
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + float(payload.get("expires_in", 1799))
        return self._token

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        token = self._authenticate()
        response = request_with_retry(
            self.session,
            "GET",
            f"{self.host}{path}",
            limiter=self.limiter,
            params={k: v for k, v in params.items() if v not in (None, "")},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 200:
            return response.json()
        # Amadeus reports "no availability" as a 400 with an errors array; that
        # is a normal outcome for a date nobody flies, not a failure to report.
        try:
            errors = response.json().get("errors", [])
            detail = "; ".join(
                f"{e.get('title', '')}: {e.get('detail', '')}".strip(": ") for e in errors
            )
        except ValueError:
            detail = response.text[:200]
        raise SourceError(f"Amadeus {path} -> {response.status_code}: {detail}")


def _stops(itinerary: dict[str, Any]) -> int:
    return max(0, len(itinerary.get("segments", [])) - 1)


def search_flights(
    client: AmadeusClient,
    trip: Trip,
    origin: str,
    destination: str,
    depart: date,
    ret: date,
    currency: str,
    max_results: int = 20,
) -> FlightQuote | None:
    """Cheapest round-trip offer meeting the trip's stop limit, or None."""
    party: Party = trip.party
    payload = client.get(
        "/v2/shopping/flight-offers",
        {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": depart.isoformat(),
            "returnDate": ret.isoformat(),
            "adults": party.adult_equivalents,
            "children": party.child_seats or None,
            "infants": party.infants or None,
            "travelClass": CABIN_MAP.get(trip.flights.cabin, "ECONOMY"),
            "nonStop": "true" if trip.flights.max_stops == 0 else None,
            "currencyCode": currency,
            "max": max_results,
        },
    )

    best: FlightQuote | None = None
    for offer in payload.get("data", []):
        itineraries = offer.get("itineraries", [])
        if len(itineraries) < 2:
            continue
        out_stops, back_stops = _stops(itineraries[0]), _stops(itineraries[1])
        if trip.flights.max_stops is not None:
            if max(out_stops, back_stops) > trip.flights.max_stops:
                continue
        price = offer.get("price", {})
        total = float(price.get("grandTotal") or price.get("total") or 0)
        if total <= 0:
            continue
        if best is not None and total >= best.total_price:
            continue
        best = FlightQuote(
            origin=origin,
            destination=destination,
            depart_date=depart,
            return_date=ret,
            total_price=total,
            currency=price.get("currency", currency),
            carriers=tuple(offer.get("validatingAirlineCodes", [])),
            stops_out=out_stops,
            stops_back=back_stops,
            source="amadeus",
            deep_link=google_flights_link(origin, destination, depart, ret),
        )
    return best


def google_flights_link(origin: str, destination: str, depart: date, ret: date) -> str:
    """A human-clickable Google Flights search, so an alert is one tap from booking."""
    return (
        "https://www.google.com/travel/flights?q="
        f"Flights%20to%20{destination}%20from%20{origin}%20on%20"
        f"{depart.isoformat()}%20through%20{ret.isoformat()}"
    )


def list_city_hotels(
    client: AmadeusClient, city_code: str, prefs: LodgingPrefs, limit: int
) -> list[dict[str, Any]]:
    """Hotel IDs in a city, filtered by star rating where the caller asked for one."""
    ratings = None
    if prefs.min_stars:
        ratings = ",".join(str(r) for r in range(int(prefs.min_stars), 6))
    payload = client.get(
        "/v1/reference-data/locations/hotels/by-city",
        {
            "cityCode": city_code,
            "radius": 50,
            "radiusUnit": "KM",
            "hotelSource": "ALL",
            "ratings": ratings,
        },
    )
    return payload.get("data", [])[:limit]


def search_lodging(
    client: AmadeusClient,
    trip: Trip,
    city_code: str,
    check_in: date,
    check_out: date,
    currency: str,
    settings: Settings,
    hotel_ids: tuple[str, ...] = (),
) -> LodgingQuote | None:
    """Cheapest qualifying hotel offer for the stay, or None."""
    prefs = trip.lodging
    nights = (check_out - check_in).days

    ids = list(hotel_ids)
    if not ids:
        hotels = list_city_hotels(client, city_code, prefs, settings.max_hotels_per_query)
        ids = [h["hotelId"] for h in hotels if h.get("hotelId")]
    if not ids:
        return None

    best: LodgingQuote | None = None
    for start in range(0, len(ids), HOTEL_BATCH):
        batch = ids[start : start + HOTEL_BATCH]
        try:
            payload = client.get(
                "/v3/shopping/hotel-offers",
                {
                    "hotelIds": ",".join(batch),
                    "adults": min(trip.party.adults + len(trip.party.children), 9),
                    "checkInDate": check_in.isoformat(),
                    "checkOutDate": check_out.isoformat(),
                    "roomQuantity": prefs.rooms,
                    "currency": currency,
                    "bestRateOnly": "true",
                },
            )
        except SourceError as exc:
            # One dead batch shouldn't lose the other batches' prices.
            log.warning("hotel batch failed for %s: %s", city_code, exc)
            continue

        for entry in payload.get("data", []):
            hotel = entry.get("hotel", {})
            for offer in entry.get("offers", []):
                price = offer.get("price", {})
                total = float(price.get("total") or 0)
                if total <= 0:
                    continue
                per_night = total / nights if nights else total
                if prefs.max_per_night and per_night > prefs.max_per_night:
                    continue
                if best is not None and total >= best.total_price:
                    continue
                policies = offer.get("policies", {})
                cancellation = policies.get("cancellations") or [{}]
                best = LodgingQuote(
                    destination=city_code,
                    check_in=check_in,
                    check_out=check_out,
                    property_name=hotel.get("name", hotel.get("hotelId", "unknown")),
                    property_id=hotel.get("hotelId", ""),
                    total_price=total,
                    currency=price.get("currency", currency),
                    nights=nights,
                    rating=_opt_float(hotel.get("rating")),
                    stars=_opt_float(hotel.get("rating")),
                    board_type=offer.get("boardType", "") or "",
                    refundable=_refundable(cancellation[0]),
                    source="amadeus",
                    deep_link=google_hotels_link(
                        hotel.get("name", ""), city_code, check_in, check_out
                    ),
                )
    return best


def _refundable(cancellation: dict[str, Any]) -> bool | None:
    policy = str(cancellation.get("type", "")).upper()
    if not policy:
        return None
    return policy != "FULL_STAY"


def google_hotels_link(name: str, city: str, check_in: date, check_out: date) -> str:
    query = requests.utils.quote(f"{name} {city}".strip())
    return (
        f"https://www.google.com/travel/search?q={query}"
        f"&checkin={check_in.isoformat()}&checkout={check_out.isoformat()}"
    )


def _opt_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
