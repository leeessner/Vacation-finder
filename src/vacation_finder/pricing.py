"""Turn a configured trip into fully-costed vacation options for one run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
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
        """The cheapest of the most fully costed options.

        Complete quotes win. Failing that, a date with its airfare priced
        beats one without, and one with its lodging beats one without —
        otherwise a date where nothing was found looks like the cheapest.
        """
        if not self.quotes:
            return None

        def coverage(q: TripQuote) -> tuple[bool, bool, bool]:
            has_fare = q.flight is not None or not q.flights_expected
            return (q.is_complete, has_fare, q.lodging is not None)

        top = max(coverage(q) for q in self.quotes)
        return min(
            (q for q in self.quotes if coverage(q) == top), key=lambda q: q.total_cost
        )


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
        if trip.has_stops:
            return self._price_stops(trip, today, result)

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

    def _price_stops(self, trip: Trip, today: date, result: RunResult) -> RunResult:
        """A trip of several stops: one flight in (and home, maybe from another
        airport), and each stop's lodging priced for its own nights."""
        windows = candidate_windows(trip, today, self.settings)
        route = " → ".join(d.code for d in trip.destinations)
        if not windows:
            result.errors.append(f"{route}: no departure dates left in the configured window")
            return result

        for depart, ret in windows:
            nights = (ret - depart).days
            if trip.flights.enabled:
                flight, notes = self._best_flight(
                    trip, trip.destinations[0].code, depart, ret, result,
                    return_from=trip.return_airport,
                )
            else:
                flight, notes = None, []
            stays: list[LodgingQuote | None] = []
            for stop, check_in, check_out in trip.legs(depart):
                stays.append(
                    self._stop_lodging(trip, stop, check_in, check_out, result, notes)
                )
            result.quotes.append(
                TripQuote(
                    trip_id=trip.id,
                    destination=route,
                    depart_date=depart,
                    return_date=ret,
                    nights=nights,
                    currency=self.settings.currency,
                    flight=flight,
                    lodging=_combine_stays(trip, depart, ret, stays, notes),
                    ground_cost=trip.ground.cost_for(nights),
                    ground_notes=trip.ground.notes,
                    travel_mode=trip.travel_mode,
                    flights_expected=trip.flights.enabled,
                    notes=notes,
                )
            )
        return result

    def _stop_lodging(
        self, trip: Trip, stop, check_in: date, check_out: date,
        result: RunResult, notes: list[str],
    ) -> LodgingQuote | None:
        """One stop's stay: searched, or its fixed estimate when Google can't
        price it (a resort that sells direct)."""
        if trip.lodging_for(stop).enabled:
            return self._best_lodging(trip, stop, check_in, check_out, result, notes)
        if stop.lodging_estimate is None:
            return None
        notes.append(
            stop.lodging_note
            or f"{stop.display}: fixed estimate, not a tracked price"
        )
        return LodgingQuote(
            destination=stop.code,
            check_in=check_in,
            check_out=check_out,
            property_name=f"{stop.display} (estimate)",
            total_price=float(stop.lodging_estimate),
            currency=self.settings.currency,
            nights=(check_out - check_in).days,
            source="estimate",
        )

    def _best_flight(
        self, trip: Trip, destination: str, depart: date, ret: date, result: RunResult,
        return_from: str = "",
    ) -> tuple[FlightQuote | None, list[str]]:
        """Cheapest fare across every home airport and both sources.

        With `return_from`, the flight home leaves from that airport instead
        (an open-jaw ticket); only Google Flights prices those.
        """
        candidates: list[FlightQuote] = []
        notes: list[str] = []
        open_jaw = bool(return_from) and return_from != destination

        for origin in trip.origins:
            api_quote = None
            # Travelpayouts is optional now; without a token, Google Flights
            # alone prices airfare and there is nothing worth reporting. Its
            # cache only holds round trips.
            if self.client.configured and not open_jaw:
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
                    trip, origin, destination, depart, ret, self.settings.currency, self.settings,
                    return_from=return_from if open_jaw else "",
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
                notes.append("airfare from this week's Google Flights check (the free daily check found none for this route)")
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
        # A stop of a multi-stop trip has its own rules; the searches below
        # read them from the trip, so hand them a trip carrying this stop's.
        prefs = trip.lodging_for(destination)
        if not prefs.enabled:
            return None
        if prefs is not trip.lodging:
            trip = replace(trip, lodging=prefs)

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


def _combine_stays(
    trip: Trip, depart: date, ret: date, stays: list[LodgingQuote | None], notes: list[str]
) -> LodgingQuote | None:
    """The stops' stays as one lodging line. Incomplete until every stop is
    priced, so a partial sum never passes for the whole trip's lodging."""
    missing = [stop.display for stop, stay in zip(trip.destinations, stays) if stay is None]
    if missing:
        notes.append("no lodging price yet for " + ", ".join(missing))
        return None
    found = [stay for stay in stays if stay is not None]
    ratings = [q.rating for q in found if q.rating is not None]
    return LodgingQuote(
        destination=" → ".join(d.code for d in trip.destinations),
        check_in=depart,
        check_out=ret,
        property_name=" + ".join(f"{q.property_name} ({q.nights}n)" for q in found),
        total_price=sum(q.total_price for q in found),
        currency=found[0].currency,
        nights=(ret - depart).days,
        rating=min(ratings) if ratings else None,
        source="+".join(sorted({q.source for q in found})),
    )
