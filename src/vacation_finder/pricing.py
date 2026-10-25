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
        insights: dict | None = None,
    ) -> None:
        self.config = config
        self.settings = config.settings
        self.client = client or TravelpayoutsClient()
        self.hotels = hotels or SerpApiClient()
        self.book = book
        self.insights = insights or {}

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
            fallback = self._insight_fare(trip, destination, depart, ret)
            if fallback is not None:
                notes.append("airfare from this week's Google Flights check (scraper failed)")
                return fallback, notes
            return None, notes
        best = min(candidates, key=lambda q: q.total_price)
        if "scaled" in best.source:
            notes.append(
                "airfare estimated: a smaller party's fare scaled to yours "
                "(Google's page lists no fares for the whole party here)"
            )
        if "extra_stop" in best.source:
            notes.append("airfare allows one more stop than your limit — none was found within it")
        return best, notes

    def _insight_fare(
        self, trip: Trip, destination: str, depart: date, ret: date
    ) -> FlightQuote | None:
        """The weekly Google Flights fare for the same dates, if the daily
        scraper came back empty — so one broken scrape doesn't blank a day."""
        insight = self.insights.get(trip.id)
        if insight is None or not insight.lowest:
            return None
        if (insight.depart, insight.return_) != (depart, ret):
            return None
        return FlightQuote(
            origin=trip.origins[0] if trip.origins else "",
            destination=destination,
            depart_date=depart,
            return_date=ret,
            total_price=float(insight.lowest),
            currency=self.settings.currency,
            source="google_flights_weekly",
        )

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
            quote = self._search_hotels(trip, destination, query, depart, ret)
        except SourceUnavailable as exc:
            message = f"hotel prices unavailable: {exc}"
            if message not in result.errors:
                result.errors.append(message)
            return self._cached_lodging(key, result, notes)
        except SourceError as exc:
            log.info("google hotels %s %s: %s", query, depart, exc)
            quote = None
        result.queries_made += 1

        note = ""
        if trip.lodging.prefer:
            quote, note = self._prefer_brand(trip, destination, depart, ret, quote, result)
        if note:
            notes.append(note)
        if self.book is not None:
            self.book.record(key, quote, note)
        return quote

    def _search_hotels(
        self, trip: Trip, destination, query: str, depart: date, ret: date, brand: str = ""
    ) -> LodgingQuote | None:
        return google_hotels.search_lodging(
            self.hotels, trip, query, depart, ret, self.settings.currency, self.settings,
            center=destination.center,
            within_miles=destination.within_miles,
            name_contains=brand,
        )

    def _prefer_brand(
        self,
        trip: Trip,
        destination,
        depart: date,
        ret: date,
        cheapest: LodgingQuote | None,
        result: RunResult,
    ) -> tuple[LodgingQuote | None, str]:
        """Swap in the preferred brand when one qualifies, noting what it costs.

        A brand search names the brand plus the plain place ("Embassy Suites
        Nashville, TN"); the brand rarely shows among the cheapest results.
        """
        place = destination.label or destination.lodging_query
        found: list[LodgingQuote] = []
        for brand in trip.lodging.prefer:
            try:
                quote = self._search_hotels(
                    trip, destination, f"{brand} {place}", depart, ret, brand=brand
                )
            except SourceError as exc:
                log.info("google hotels %s %s %s: %s", brand, place, depart, exc)
                quote = None
            result.queries_made += 1
            if quote is not None:
                found.append(quote)

        brands = " / ".join(trip.lodging.prefer)
        if not found:
            where = (
                f" within {destination.within_miles:g} mi" if destination.within_miles else ""
            )
            return cheapest, f"no {brands}{where} met the rules for these dates"
        preferred = min(found, key=lambda q: q.total_price)
        cheapest_is_preferred = cheapest is not None and any(
            b.lower() in cheapest.property_name.lower() for b in trip.lodging.prefer
        )
        if cheapest is None or cheapest_is_preferred:
            # Nothing cheaper to compare against: the preferred brand won outright.
            if cheapest is not None and cheapest.total_price < preferred.total_price:
                return cheapest, ""
            return preferred, ""
        gap = preferred.total_price - cheapest.total_price
        word = "more" if gap >= 0 else "less"
        return preferred, (
            f"{brands}: ${abs(gap):,.0f} {word} than the cheapest qualifying option, "
            f"{cheapest.property_name} (${cheapest.total_price:,.0f})"
        )

    def _cached_lodging(
        self, key: str, result: RunResult, notes: list[str]
    ) -> LodgingQuote | None:
        if self.book is None:
            return None
        quote, age = self.book.cached(key)
        if quote is not None and age:
            notes.append(f"hotel price checked {age} day(s) ago")
        if quote is not None and self.book.note(key):
            notes.append(self.book.note(key))
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
