"""Turn a configured trip into fully-costed vacation options for one run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .config import Config, Trip
from .dates import candidate_windows
from .lodging import LodgingBook, slot_key
from .models import FlightQuote, LodgingQuote, TripQuote
from .sources import google_flights, google_hotels
from .sources.base import SourceError, SourceUnavailable
from .sources.google_hotels import SerpApiClient
from .sources.travelpayouts import TravelpayoutsClient, search_flights

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
    """Prices trips while keeping the free-tier API call count in check.

    `book` decides which hotel slots get a live search this run and supplies
    the last price for the rest. Without one, every slot is searched — which
    is what tests and one-off use want.
    """

    def __init__(
        self,
        config: Config,
        client: TravelpayoutsClient | None = None,
        hotels: SerpApiClient | None = None,
        book: LodgingBook | None = None,
    ) -> None:
        self.config = config
        self.settings = config.settings
        self.client = client or TravelpayoutsClient()
        self.hotels = hotels or SerpApiClient()
        self.book = book

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
                if trip.flights.enabled:
                    flight, notes = self._best_flight(
                        trip, destination.code, depart, ret, result
                    )
                else:
                    flight, notes = None, []
                lodging = self._best_lodging(trip, destination, depart, ret, result, notes)
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
                        travel_mode=trip.travel_mode,
                        flights_expected=trip.flights.enabled,
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
            api_quote = None
            # Travelpayouts is optional now; without a token, Google Flights
            # alone prices airfare and there is nothing worth reporting.
            if self.client.configured:
                try:
                    api_quote = search_flights(
                        self.client,
                        trip,
                        origin,
                        destination,
                        depart,
                        ret,
                        self.settings.currency,
                        self.settings,
                    )
                    result.queries_made += 1
                except SourceUnavailable as exc:
                    result.errors.append(f"travelpayouts unavailable: {exc}")
                except SourceError as exc:
                    log.info(
                        "travelpayouts flights %s->%s %s: %s", origin, destination, depart, exc
                    )
            if api_quote:
                candidates.append(api_quote)

            if self.settings.google_flights_enabled:
                scraped = google_flights.search_flights(
                    trip, origin, destination, depart, ret, self.settings.currency, self.settings
                )
                result.queries_made += 1
                if scraped:
                    candidates.append(scraped)
                    if api_quote:
                        note = _cross_check_note(origin, api_quote, scraped)
                        if note:
                            notes.append(note)

        if not candidates:
            return None, notes
        return min(candidates, key=lambda q: q.total_price), notes

    def _best_lodging(
        self,
        trip: Trip,
        destination,
        depart: date,
        ret: date,
        result: RunResult,
        notes: list[str],
    ) -> LodgingQuote | None:
        if not trip.lodging.enabled:
            return None

        query = destination.lodging_query
        key = slot_key(trip.id, query, depart, ret)
        if self.book is not None and not self.book.should_search(key):
            return self._cached_lodging(key, result, notes)

        try:
            quote = google_hotels.search_lodging(
                self.hotels, trip, query, depart, ret, self.settings.currency, self.settings
            )
        except SourceUnavailable as exc:
            message = f"hotel prices unavailable: {exc}"
            if message not in result.errors:
                result.errors.append(message)
            return self._cached_lodging(key, result, notes)
        except SourceError as exc:
            log.info("google hotels %s %s: %s", query, depart, exc)
            quote = None
        result.queries_made += 1
        if self.book is not None:
            self.book.record(key, quote)
        return quote

    def _cached_lodging(
        self, key: str, result: RunResult, notes: list[str]
    ) -> LodgingQuote | None:
        if self.book is None:
            return None
        quote, age = self.book.cached(key)
        if quote is not None and age:
            notes.append(f"hotel price checked {age} day(s) ago")
        if quote is None and self.book.unavailable_reason:
            message = f"hotel prices unavailable: {self.book.unavailable_reason}"
            if message not in result.errors:
                result.errors.append(message)
        return quote


def _cross_check_note(origin: str, cached: FlightQuote, scraped: FlightQuote) -> str:
    """Flag when the two sources disagree enough to be worth a human look.

    Some gap is expected and healthy: Travelpayouts is cached and per-adult
    scaled, Google Flights is live and prices the real party. A large gap
    usually means the cache is stale or one source has drifted.
    """
    if cached.total_price <= 0:
        return ""
    gap = (scraped.total_price - cached.total_price) / cached.total_price * 100
    if abs(gap) < 15:
        return ""
    cheaper = "Google Flights" if gap < 0 else "cached"
    return (
        f"{origin}: sources disagree by {abs(gap):.0f}% "
        f"(cached {cached.total_price:.0f} vs Google {scraped.total_price:.0f}); "
        f"{cheaper} is cheaper"
    )
