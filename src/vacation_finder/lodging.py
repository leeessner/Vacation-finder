"""Spending a small monthly hotel-search budget well.

SerpApi's free plan is 250 searches a month, about 8 a day, and the tracker
has roughly 25 "slots" to price — every trip times every sampled date window.
So not every slot can be searched every day. Instead:

- Each run refreshes the slots that have gone longest without a search, within
  the searches left for hotels after this run's flight insights (see
  `settings.searches_per_run`), and never below the reserve kept back for
  Doctor and manual runs. A slot with a preferred brand costs one search
  more per brand.
- Every other slot reuses its last price, for up to
  `settings.hotel_price_max_age_days`. A hotel stay five months out moves
  over days, not hours, so a price two or three days old is still the price;
  the reports say how old it is.

The cache lives in data/state/lodging.json and is committed with the rest of
the day's data, so it survives between runs on fresh GitHub runners.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from .config import Config, Destination, Trip
from .dates import candidate_windows
from .models import LodgingQuote

log = logging.getLogger(__name__)

STATE_NAME = "lodging"


def slot_key(trip_id: str, query: str, check_in: date, check_out: date) -> str:
    return f"{trip_id}|{query}|{check_in.isoformat()}|{check_out.isoformat()}"


def lodging_slots(config: Config, today: date) -> list[tuple[Trip, Destination, date, date]]:
    """Every (trip, destination, check-in, check-out) the active trips will price."""
    slots = []
    for trip in config.active_trips:
        windows = candidate_windows(trip, today, config.settings)
        if config.settings.hotel_windows_per_trip is not None:
            windows = windows[: max(0, config.settings.hotel_windows_per_trip)]
        if trip.has_stops:
            # Each stop is its own stay, with its own dates and rules; a stop
            # Google can't price (lodging disabled) costs nothing.
            for depart, _ in windows:
                for stop, check_in, check_out in trip.legs(depart):
                    if trip.lodging_for(stop).enabled:
                        slots.append((trip, stop, check_in, check_out))
            continue
        if not trip.lodging.enabled:
            continue
        for destination in trip.destinations:
            for check_in, check_out in windows:
                slots.append((trip, destination, check_in, check_out))
    return slots


@dataclass
class LodgingBook:
    """Which slots to search this run, and the last price for the rest."""

    today: date
    max_age_days: int
    refresh: set[str] = field(default_factory=set)
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    unavailable_reason: str = ""

    @classmethod
    def plan(
        cls,
        config: Config,
        today: date,
        allowance: int,
        entries: dict[str, dict[str, Any]] | None = None,
        unavailable_reason: str = "",
        priority: Iterable[str] = (),
        recheck_days: int | None = None,
    ) -> "LodgingBook":
        """`priority` slots (a first look at a new trip) go to the front of the
        queue; everything else keeps its stalest-first order behind them."""
        entries = dict(entries or {})
        costs = {
            slot_key(trip.id, dest.lodging_query, check_in, check_out): slot_cost(trip, dest)
            for trip, dest, check_in, check_out in lodging_slots(config, today)
        }
        # Never-searched slots first, then the stalest. Python's sort is
        # stable, so ties keep trip order and the choice is deterministic.
        # A first-look slot already priced today isn't searched twice.
        done_today = {
            k for k, e in entries.items()
            if e.get("checked_on") == today.isoformat() and e.get("quote")
        }
        first = [k for k in dict.fromkeys(priority) if k in costs and k not in done_today]
        # A price newer than hotel_recheck_days waits its turn (a weekly
        # cadence); a first look always goes ahead.
        recheck = config.settings.hotel_recheck_days if recheck_days is None else recheck_days
        cutoff = (today - timedelta(days=recheck)).isoformat() if recheck else ""
        ordered = first + sorted(
            (k for k in costs if k not in set(first)
             and not (cutoff and entries.get(k, {}).get("checked_on", "") > cutoff)),
            key=lambda k: entries.get(k, {}).get("checked_on", ""),
        )
        refresh: set[str] = set()
        budget = max(0, allowance)
        for key in ordered:
            if costs[key] > budget:
                break          # don't skip ahead: the stalest slot keeps its place
            refresh.add(key)
            budget -= costs[key]
        book = cls(
            today=today,
            max_age_days=config.settings.hotel_price_max_age_days,
            refresh=refresh,
            entries=_prune(entries, set(costs)),
            unavailable_reason=unavailable_reason,
        )
        log.info(
            "hotel searches this run: %d slot(s) of %d, %d search(es)",
            len(refresh), len(costs), max(0, allowance) - budget,
        )
        return book

    @classmethod
    def load(
        cls, config: Config, today: date, allowance: int, unavailable_reason: str = "",
        priority: Iterable[str] = (), recheck_days: int | None = None,
    ) -> "LodgingBook":
        from .storage import load_state  # storage imports pricing, which imports this

        state = load_state(STATE_NAME)
        return cls.plan(
            config, today, allowance, state.get("slots", {}), unavailable_reason, priority,
            recheck_days,
        )

    def save(self) -> None:
        from .storage import save_state

        save_state(STATE_NAME, {"slots": self.entries})

    def should_search(self, key: str) -> bool:
        return key in self.refresh

    def record(self, key: str, quote: LodgingQuote | None, note: str = "") -> None:
        """Store a fresh result. A search that found nothing is stored too,
        so an empty slot isn't searched again tomorrow at the top of the queue."""
        self.entries[key] = {
            "checked_on": self.today.isoformat(),
            "quote": quote.to_dict() if quote else None,
            "note": note,
        }

    def note(self, key: str) -> str:
        """What was worth saying when the slot was last searched."""
        return str((self.entries.get(key) or {}).get("note") or "")

    def cached(self, key: str) -> tuple[LodgingQuote | None, int | None]:
        """The last price for a slot and its age in days, if still usable."""
        entry = self.entries.get(key)
        if not entry or not entry.get("quote"):
            return None, None
        try:
            age = (self.today - date.fromisoformat(entry["checked_on"])).days
        except (KeyError, ValueError):
            return None, None
        if age > self.max_age_days:
            return None, age
        return _quote_from_dict(entry["quote"]), age


def slot_cost(trip: Trip, destination: Destination | None = None) -> int:
    """Searches one check of this trip's slot (or this stop's) takes."""
    prefs = trip.lodging_for(destination) if destination is not None else trip.lodging
    return 1 + len(prefs.prefer)


def _prune(entries: dict[str, dict[str, Any]], live: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Drop slots no trip prices any more (dates passed, trip parked)."""
    live = set(live)
    return {k: v for k, v in entries.items() if k in live}


def _quote_from_dict(raw: dict[str, Any]) -> LodgingQuote | None:
    try:
        return LodgingQuote(
            destination=str(raw["destination"]),
            check_in=date.fromisoformat(raw["check_in"]),
            check_out=date.fromisoformat(raw["check_out"]),
            property_name=str(raw["property_name"]),
            total_price=float(raw["total_price"]),
            currency=str(raw["currency"]),
            property_id=str(raw.get("property_id", "")),
            nights=int(raw.get("nights", 0)),
            rating=raw.get("rating"),
            stars=raw.get("stars"),
            board_type=str(raw.get("board_type", "")),
            refundable=raw.get("refundable"),
            source=str(raw.get("source", "unknown")),
            deep_link=str(raw.get("deep_link", "")),
            distance_miles=raw.get("distance_miles"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def run_allowance(searches_left: int | None, per_run: int, reserve: int) -> int:
    """How many searches this run may spend.

    `searches_left` comes from SerpApi's free account endpoint; when it can't
    be read the per-run cap alone applies, which is sized to fit the month.
    """
    if searches_left is None:
        return max(0, per_run)
    return max(0, min(per_run, searches_left - reserve))


def first_look_slots(config: Config, today: date, trip_ids: Iterable[str]) -> dict[str, int]:
    """The hotel slots for each listed trip's first date window, with their
    cost: one full set of prices per trip, the cheapest way to see it once."""
    wanted = set(trip_ids)
    out: dict[str, int] = {}
    for trip in config.active_trips:
        if trip.id not in wanted:
            continue
        windows = candidate_windows(trip, today, config.settings)
        if not windows:
            continue
        depart, ret = windows[0]
        stays = trip.legs(depart) if trip.has_stops else [
            (d, depart, ret) for d in trip.destinations
        ]
        for stop, check_in, check_out in stays:
            if trip.lodging_for(stop).enabled:
                out[slot_key(trip.id, stop.lodging_query, check_in, check_out)] = slot_cost(trip, stop)
    return out
