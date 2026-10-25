"""Book now, or wait? A plain-language outlook for each trip.

Nobody can predict next month's fare, and nothing here pretends to. What the
outlook does is combine the three things that genuinely help:

1. Where today's fare sits against Google's typical range for the route —
   the one signal available from day one.
2. Which way the tracker's own prices have been moving (`stats.trend`).
3. How far away the trip is, against when long-haul fares are usually
   lowest (`settings.booking_window_months`, 5–7 months by default — rough
   guidance from published booking studies, not a law).

Every outlook says why, and how much evidence it rests on, so a "wait" built
on nine days of data reads differently from one built on three months.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Settings, Trip
from .insights import FlightInsight
from .stats import Trend

BOOK = "Good time to book"
SOON = "Book soon"
WATCH = "Keep watching"
WAIT = "Wait if you can"

# A move this steep, sustained, is worth acting on.
STRONG_PCT_PER_WEEK = 2.0


@dataclass
class Outlook:
    label: str
    reasons: list[str] = field(default_factory=list)
    confidence: str = "low"          # low / medium / high

    @property
    def summary(self) -> str:
        return f"{self.label} — " + "; ".join(self.reasons) if self.reasons else self.label


def months_until(today: date, depart: date) -> float:
    return (depart - today).days / 30.44


def outlook(
    trip: Trip,
    settings: Settings,
    today: date,
    depart: date | None,
    *,
    flight_price: float | None = None,
    insight: FlightInsight | None = None,
    flight_trend: Trend | None = None,
    lodging_trend: Trend | None = None,
    days_of_history: int = 0,
    currency_symbol: str = "$",
) -> Outlook:
    """The outlook for one trip. Flights drive it when they're priced;
    otherwise it's about lodging alone."""
    if depart is None:
        return Outlook(WATCH, ["no dates priced yet"])

    flying = trip.travel_mode == "air" and trip.flights.enabled
    if flying:
        result = _flight_outlook(
            settings, today, depart, flight_price, insight, flight_trend, currency_symbol
        )
    else:
        result = _lodging_outlook(lodging_trend)

    evidence = days_of_history + (30 if insight else 0)
    result.confidence = "high" if evidence >= 60 else "medium" if evidence >= 21 else "low"
    if days_of_history == 0:
        result.reasons.append("no prices tracked yet")
    elif days_of_history < 14:
        result.reasons.append(f"only {days_of_history} day(s) of tracking so far")
    return result


def _flight_outlook(
    settings: Settings,
    today: date,
    depart: date,
    price: float | None,
    insight: FlightInsight | None,
    trend: Trend | None,
    sym: str,
) -> Outlook:
    early, late = settings.booking_window_months[1], settings.booking_window_months[0]
    months = months_until(today, depart)
    reasons: list[str] = []

    level = insight.level if insight else ""
    if insight and price and insight.typical_low and price < insight.typical_low:
        level = "low"
    if insight and price and insight.typical_high and price > insight.typical_high:
        level = "high"
    if insight and insight.typical_low and insight.typical_high:
        reasons.append(
            f"Google's typical range is {sym}{insight.typical_low:,.0f}–"
            f"{sym}{insight.typical_high:,.0f}"
        )

    rising = trend is not None and trend.direction == "rising"
    falling = trend is not None and trend.direction == "falling"
    strong_rise = rising and trend.pct_per_week >= STRONG_PCT_PER_WEEK and trend.days >= 14
    if strong_rise:
        reasons.append(f"fares have been climbing {trend.pct_per_week:.1f}% a week")

    if level == "low":
        label = BOOK
        reasons.insert(0, "the fare is low for this route and season")
    elif months < late:
        label = SOON
        reasons.insert(
            0, f"{months:.0f} months out — fares usually climb inside {late} months"
        )
    elif level == "high" and not strong_rise:
        label = WAIT
        reasons.insert(0, "the fare is high for this route and season")
    elif months > early:
        label = SOON if strong_rise else WATCH
        best_from = _months_before(depart, early)
        reasons.insert(
            0,
            f"{months:.0f} months out — long-haul fares are usually lowest "
            f"{late}–{early} months before departure (from about {best_from:%b %Y})",
        )
    else:
        label = SOON if (strong_rise or not falling) else WATCH
        reasons.insert(0, f"{months:.0f} months out — inside the usual best window")
        if falling:
            reasons.append("still falling, so waiting a little may pay")
    return Outlook(label, reasons)


def _lodging_outlook(trend: Trend | None) -> Outlook:
    if trend is None:
        return Outlook(WATCH, ["not enough hotel history for a trend yet"])
    if trend.direction == "rising" and trend.pct_per_week >= STRONG_PCT_PER_WEEK:
        return Outlook(
            SOON, ["hotel prices are climbing quickly — lock in a refundable rate"]
        )
    if trend.direction == "falling":
        return Outlook(WATCH, ["hotel prices are falling — no rush"])
    if trend.direction == "rising":
        return Outlook(WATCH, ["hotel prices are creeping up — worth deciding soon"])
    return Outlook(WATCH, ["hotel prices are steady"])


def _months_before(depart: date, months: int) -> date:
    year, month = depart.year, depart.month - months
    while month < 1:
        month += 12
        year -= 1
    return date(year, month, 1)
