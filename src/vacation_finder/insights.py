"""Google Flights' own judgement of a fare: low, typical or high, and the
typical range for the route and dates.

This is the one piece of price context that doesn't have to be built up day
by day: Google already knows what a June STL–Paris fare for six usually
costs ($7,800–$9,400 when this was written). It costs a SerpApi search, so
each flying trip is checked once a week (`settings.flight_insight_days`), on
its first sampled date window. Daily airfare still comes from the keyless
Google Flights scraper.

Google returns no price history for trips this far out — that was checked
live — so the trend line comes from the tracker's own daily prices.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from .config import Config, Trip
from .dates import candidate_windows
from .sources.base import SourceError

log = logging.getLogger(__name__)

STATE_NAME = "flight_insights"

# At most this many weekly checks a run, so they spread across the week
# instead of all falling due on one day and starving that day's hotels.
MAX_PER_RUN = 2

# SerpApi's `stops` codes: 0 any, 1 nonstop, 2 one stop or fewer, 3 two or fewer.
STOPS_CODE = {None: 0, 0: 1, 1: 2, 2: 3}


@dataclass(frozen=True)
class FlightInsight:
    trip_id: str
    checked_on: date
    depart: date
    return_: date
    lowest: float | None
    level: str                       # "low", "typical", "high" or ""
    typical_low: float | None
    typical_high: float | None

    def phrase(self, currency_symbol: str = "$") -> str:
        parts = []
        if self.level:
            parts.append(f"Google rates this fare {self.level}")
        if self.typical_low and self.typical_high:
            parts.append(
                f"typical {currency_symbol}{self.typical_low:,.0f}–"
                f"{currency_symbol}{self.typical_high:,.0f} for the party"
            )
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        for k in ("checked_on", "depart", "return_"):
            raw[k] = raw[k].isoformat()
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FlightInsight | None":
        try:
            return cls(
                trip_id=str(raw["trip_id"]),
                checked_on=date.fromisoformat(raw["checked_on"]),
                depart=date.fromisoformat(raw["depart"]),
                return_=date.fromisoformat(raw["return_"]),
                lowest=raw.get("lowest"),
                level=str(raw.get("level") or ""),
                typical_low=raw.get("typical_low"),
                typical_high=raw.get("typical_high"),
            )
        except (KeyError, TypeError, ValueError):
            return None


def flying_trips(config: Config) -> list[Trip]:
    return [t for t in config.active_trips if t.flights.enabled and t.travel_mode == "air"]


def load_insights() -> dict[str, FlightInsight]:
    from .storage import load_state

    out = {}
    for trip_id, raw in (load_state(STATE_NAME) or {}).items():
        insight = FlightInsight.from_dict(raw) if isinstance(raw, dict) else None
        if insight:
            out[trip_id] = insight
    return out


def save_insights(insights: dict[str, FlightInsight]) -> None:
    from .storage import save_state

    save_state(STATE_NAME, {k: v.to_dict() for k, v in sorted(insights.items())})


def due(config: Config, insights: dict[str, FlightInsight], today: date) -> list[Trip]:
    """Flying trips whose insight is missing or older than the refresh interval,
    oldest first."""
    every = config.settings.flight_insight_days

    def age(trip: Trip) -> int:
        known = insights.get(trip.id)
        if known is None:
            return 10_000
        # A check for other dates (the trip's length or window changed since)
        # can't stand in for today's fare, however recent it is.
        windows = candidate_windows(trip, today, config.settings)
        if windows and (known.depart, known.return_) != windows[0]:
            return 10_000
        return (today - known.checked_on).days

    stale = [t for t in flying_trips(config) if age(t) >= every]
    return sorted(stale, key=age, reverse=True)


def fetch(client, trip: Trip, config: Config, today: date) -> FlightInsight | None:
    """One SerpApi Google Flights search for the trip's first date window."""
    windows = candidate_windows(trip, today, config.settings)
    if not windows or not trip.origins:
        return None
    depart, ret = windows[0]
    party = trip.party
    arrive, leave = trip.destinations[0].code, trip.return_airport
    if leave != arrive:
        # Open-jaw: a multi-city search, out to the first stop and home from
        # the last. Google may not give a typical range for these; the
        # lowest fare still comes back.
        trip_params = {
            "type": 3,
            "multi_city_json": json.dumps([
                {"departure_id": ",".join(trip.origins), "arrival_id": arrive,
                 "date": depart.isoformat()},
                {"departure_id": leave, "arrival_id": ",".join(trip.origins),
                 "date": ret.isoformat()},
            ]),
        }
    else:
        trip_params = {
            "departure_id": ",".join(trip.origins),
            "arrival_id": arrive,
            "outbound_date": depart.isoformat(),
            "return_date": ret.isoformat(),
            "type": 1,
        }
    params = {
        **trip_params,
        "adults": party.adult_equivalents,
        "children": party.child_seats,
        "infants_on_lap": party.infants,
        "stops": STOPS_CODE.get(trip.flights.max_stops, 0),
        "currency": config.settings.currency,
        "gl": "us",
        "hl": "en",
    }
    try:
        payload = client.search(params, engine="google_flights")
    except SourceError as exc:
        log.info("flight insight %s: %s", trip.id, exc)
        return None
    info = payload.get("price_insights") or {}
    typical = info.get("typical_price_range") or [None, None]
    lowest = info.get("lowest_price")
    if lowest is None:
        prices = [
            o.get("price")
            for group in ("best_flights", "other_flights")
            for o in payload.get(group) or []
            if isinstance(o, dict) and isinstance(o.get("price"), (int, float))
        ]
        lowest = min(prices) if prices else None
    return FlightInsight(
        trip_id=trip.id,
        checked_on=today,
        depart=depart,
        return_=ret,
        lowest=float(lowest) if lowest is not None else None,
        level=str(info.get("price_level") or ""),
        typical_low=_num(typical[0] if len(typical) > 0 else None),
        typical_high=_num(typical[1] if len(typical) > 1 else None),
    )


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
