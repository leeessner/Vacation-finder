"""Alert rules and the de-duplication that keeps them worth reading.

An alert you get every day is an alert you stop opening, so each rule has a
cooldown. The exception is a price that keeps falling: if it drops materially
below what we last alerted on, that's new information and it fires again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .config import Trip
from .stats import PriceAssessment
from .storage import HistoryRow, load_state, save_state

STATE_NAME = "alerts"
# A re-alert during cooldown needs the price to have fallen at least this much
# further, so rounding noise doesn't retrigger a rule.
RETRIGGER_DROP_PCT = 2.0


@dataclass(frozen=True)
class Alert:
    trip_id: str
    trip_name: str
    kind: str
    headline: str
    detail: str
    total: float
    row: HistoryRow
    assessment: PriceAssessment

    @property
    def key(self) -> str:
        return f"{self.trip_id}:{self.kind}"


def evaluate(
    trip: Trip, row: HistoryRow, assessment: PriceAssessment, currency: str = "$"
) -> list[Alert]:
    """Every rule this observation trips, before cooldown filtering."""
    rules = trip.alerts
    alerts: list[Alert] = []

    def add(kind: str, headline: str, detail: str) -> None:
        alerts.append(
            Alert(
                trip_id=trip.id,
                trip_name=trip.name,
                kind=kind,
                headline=headline,
                detail=detail,
                total=row.total,
                row=row,
                assessment=assessment,
            )
        )

    enough_history = assessment.observations >= rules.min_observations

    if rules.total_below is not None and row.total <= rules.total_below:
        add(
            "below_target",
            f"{trip.name} is under your {currency}{rules.total_below:,.0f} target",
            f"Total {currency}{row.total:,.0f} for {row.nights} nights "
            f"departing {row.depart_date:%b %d}.",
        )

    if rules.flight_below is not None and 0 < row.flight_total <= rules.flight_below:
        add(
            "flight_below_target",
            f"{trip.name} flights are under {currency}{rules.flight_below:,.0f}",
            f"Airfare {currency}{row.flight_total:,.0f} for the whole party "
            f"({row.flight_carriers or 'carrier n/a'}), departing {row.depart_date:%b %d}.",
        )

    if rules.all_time_low and assessment.is_all_time_low and enough_history:
        add(
            "all_time_low",
            f"{trip.name} hit an all-time low",
            f"{currency}{row.total:,.0f} is the cheapest in "
            f"{assessment.observations} observations since "
            f"{assessment.first_observed:%b %d, %Y}.",
        )

    drop = rules.drop_pct_vs_median
    delta = assessment.delta_vs_recent_median_pct
    if drop is not None and delta is not None and enough_history and delta <= -drop:
        add(
            "price_drop",
            f"{trip.name} dropped {abs(delta):.0f}% below its recent average",
            f"Now {currency}{row.total:,.0f} against a 30-day median of "
            f"{currency}{assessment.recent_median:,.0f}.",
        )

    ceiling = rules.percentile_below
    if (
        ceiling is not None
        and assessment.percentile is not None
        and enough_history
        and assessment.percentile <= ceiling
        and not assessment.is_all_time_low
    ):
        add(
            "cheap_percentile",
            f"{trip.name} is in the cheapest {assessment.percentile:.0f}% we've seen",
            f"{currency}{row.total:,.0f} vs a median of "
            f"{currency}{assessment.median:,.0f}.",
        )

    return alerts


def filter_new(
    alerts: list[Alert], trips: dict[str, Trip], today: date | None = None, state: dict | None = None
) -> tuple[list[Alert], dict]:
    """Drop alerts still inside their cooldown unless the price fell further."""
    today = today or datetime.now(timezone.utc).date()
    state = dict(state if state is not None else load_state(STATE_NAME))
    fresh: list[Alert] = []

    for alert in alerts:
        trip = trips.get(alert.trip_id)
        cooldown = trip.alerts.cooldown_days if trip else 3
        previous = state.get(alert.key)

        if previous:
            try:
                last_fired = date.fromisoformat(previous["last_fired"])
            except (KeyError, ValueError):
                last_fired = None
            last_price = float(previous.get("last_price", 0) or 0)
            within_cooldown = last_fired is not None and (today - last_fired).days < cooldown
            dropped_further = last_price > 0 and alert.total <= last_price * (
                1 - RETRIGGER_DROP_PCT / 100
            )
            if within_cooldown and not dropped_further:
                continue

        fresh.append(alert)
        state[alert.key] = {
            "last_fired": today.isoformat(),
            "last_price": round(alert.total, 2),
        }

    return fresh, state


def persist(state: dict) -> None:
    save_state(STATE_NAME, state)
