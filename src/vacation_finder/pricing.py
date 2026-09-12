"""Turn a configured trip into fully-costed vacation options for one run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .config import Config, Trip
from .dates import candidate_windows
from .models import FlightQuote, LodgingQuote, TripQuote
from .sources import google_flights
from .sources.amadeus import AmadeusClient, search_flights, search_lodging
from .sources.base import SourceError, SourceUnavailable

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Everything one trip's pricing run produced, including what went wrong."""

    trip_id: str
    captured_at: datetime
    quotes: list[TripQuote] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    queries_made: int = 0

    @property
    def complete_quotes(self) -> list[TripQuote]:
        return [q for q in self.quotes if q.is_complete]

    @property
    def best(self) -> TripQuote | None:
        """Cheapest option we could fully cost; falls back to partial quotes."""
        pool = self.complete_quotes or self.quotes
        return min(pool, key=lambda q: q.total_cost) if pool else None


class TripPricer:
    """Prices trips while keeping the free-tier API call count in check."""

    def __init__(self, config: Config, client: AmadeusClient | None = None) -> None:
        self.config = config
        self.settings = config.settings
        self.client = client or AmadeusClient(env=config.settings.amadeus_env)
        self._hotel_id_cache: dict[tuple[str, str], tuple[str, ...]] = {}

    def price_trip(self, trip: Trip, today: date | None = None) -> RunResult:
        today = today or datetime.now(timezone.utc).date()
        result = RunResult(trip_id=trip.id, captured_at=datetime.now(timezone.utc))

        for destination in trip.destinations:
            windows = candidate_windows(trip, today, self.settings)
            if not windows:
                result.errors.append(
                    f"{destination.code}: no departure dates left in the configured window"
                )
                continue

            for depart, ret in windows:
                nights = (ret - depart).days
                flight, notes = self._best_flight(trip, destination.code, depart, ret, result)
                lodging = self._best_lodging(trip, destination, depart, ret, result)
                result.quotes.append(
                    TripQuote(
                        trip_id=trip.id,
                        destination=destination.code,
                        depart_date=depart,
                        return_date=ret,
                        nights=nights,
                        currency=self.settings.currency,
                        flight=flight,
                        lodging=lodging,
                        ground_cost=trip.ground.cost_for(nights),
                        ground_notes=trip.ground.notes,
                        notes=notes,
                    )
                )
        return result

    def _best_flight(
        self, trip: Trip, destination: str, depart: date, ret: date, result: RunResult
    ) -> tuple[FlightQuote | None, list[str]]:
        """Cheapest fare across every home airport and both sources."""
        candidates: list[FlightQuote] = []
        notes: list[str] = []

        for origin in trip.origins:
            amadeus_quote = None
            try:
                amadeus_quote = search_flights(
                    self.client, trip, origin, destination, depart, ret, self.settings.currency
                )
                result.queries_made += 1
            except SourceUnavailable as exc:
                result.errors.append(f"amadeus unavailable: {exc}")
            except SourceError as exc:
                log.info("amadeus flights %s->%s %s: %s", origin, destination, depart, exc)
            if amadeus_quote:
                candidates.append(amadeus_quote)

            if self.settings.google_flights_enabled:
                scraped = google_flights.search_flights(
                    trip, origin, destination, depart, ret, self.settings.currency, self.settings
                )
                result.queries_made += 1
                if scraped:
                    candidates.append(scraped)
                    if amadeus_quote:
                        notes.append(
                            _cross_check_note(origin, amadeus_quote, scraped)
                        )

        if not candidates:
            return None, notes
        return min(candidates, key=lambda q: q.total_price), notes

    def _best_lodging(
        self, trip: Trip, destination, depart: date, ret: date, result: RunResult
    ) -> LodgingQuote | None:
        if not trip.lodging.enabled:
            return None

        city = destination.hotel_city
        hotel_ids = destination.hotel_ids or self._cached_hotel_ids(trip, city)
        try:
            quote = search_lodging(
                self.client,
                trip,
                city,
                depart,
                ret,
                self.settings.currency,
                self.settings,
                hotel_ids=hotel_ids,
            )
            result.queries_made += 1
            return quote
        except SourceUnavailable as exc:
            result.errors.append(f"amadeus unavailable: {exc}")
        except SourceError as exc:
            log.info("amadeus lodging %s %s: %s", city, depart, exc)
        return None

    def _cached_hotel_ids(self, trip: Trip, city: str) -> tuple[str, ...]:
        """The city's hotel list barely changes; fetch it once per run, not per date."""
        key = (city, str(trip.lodging.min_stars))
        if key in self._hotel_id_cache:
            return self._hotel_id_cache[key]

        from .sources.amadeus import list_city_hotels

        try:
            hotels = list_city_hotels(
                self.client, city, trip.lodging, self.settings.max_hotels_per_query
            )
            ids = tuple(h["hotelId"] for h in hotels if h.get("hotelId"))
        except (SourceError, SourceUnavailable) as exc:
            log.info("hotel list for %s failed: %s", city, exc)
            ids = ()
        self._hotel_id_cache[key] = ids
        return ids


def _cross_check_note(origin: str, amadeus: FlightQuote, scraped: FlightQuote) -> str:
    """Flag when the two sources disagree enough to be worth a human look."""
    if amadeus.total_price <= 0:
        return ""
    gap = (scraped.total_price - amadeus.total_price) / amadeus.total_price * 100
    if abs(gap) < 10:
        return ""
    cheaper = "Google Flights" if gap < 0 else "Amadeus"
    return (
        f"{origin}: sources disagree by {abs(gap):.0f}% "
        f"(Amadeus {amadeus.total_price:.0f} vs Google {scraped.total_price:.0f}); "
        f"{cheaper} is cheaper"
    )
