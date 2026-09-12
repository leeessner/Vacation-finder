"""Turn a configured trip into fully-costed vacation options for one run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .config import Config, Trip
from .dates import candidate_windows
from .models import FlightQuote, LodgingQuote, TripQuote
from .sources import google_flights
from .sources.base import SourceError, SourceUnavailable
from .sources.travelpayouts import (
    TravelpayoutsClient,
    search_flights,
    search_lodging,
)

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

    def __init__(self, config: Config, client: TravelpayoutsClient | None = None) -> None:
        self.config = config
        self.settings = config.settings
        self.client = client or TravelpayoutsClient()

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
                log.info("travelpayouts flights %s->%s %s: %s", origin, destination, depart, exc)
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
        self, trip: Trip, destination, depart: date, ret: date, result: RunResult
    ) -> LodgingQuote | None:
        if not trip.lodging.enabled:
            return None

        # Hotellook resolves plain city names, so the label is a better query
        # than an airport code where one is set (PAR beats CDG for Paris).
        city = destination.label or destination.hotel_city
        try:
            quote = search_lodging(
                self.client,
                trip,
                city,
                depart,
                ret,
                self.settings.currency,
                self.settings,
            )
            result.queries_made += 1
            return quote
        except SourceUnavailable as exc:
            result.errors.append(f"travelpayouts unavailable: {exc}")
        except SourceError as exc:
            log.info("travelpayouts lodging %s %s: %s", city, depart, exc)
        return None


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
