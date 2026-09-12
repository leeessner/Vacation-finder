"""Turning a price series into a judgement: is today's number actually good?

The whole point of tracking daily is that "$3,900" means nothing on its own.
It means something once you know the trip has ranged $3,600-$5,400 over four
months and today sits in the bottom 8% of everything we've seen.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from .storage import HistoryRow

# Below this many observations we report a baseline is still forming rather
# than pretending a percentile computed from three points means anything.
MIN_MEANINGFUL_OBSERVATIONS = 5

VERDICTS = {
    "building": "building baseline",
    "all_time_low": "all-time low",
    "excellent": "excellent",
    "good": "good",
    "typical": "typical",
    "high": "high",
    "very_high": "very high",
}


@dataclass(frozen=True)
class PriceAssessment:
    current: float
    observations: int
    verdict: str
    percentile: float | None = None
    all_time_low: float | None = None
    all_time_high: float | None = None
    median: float | None = None
    recent_median: float | None = None
    previous: float | None = None
    delta_vs_previous: float | None = None
    delta_vs_recent_median_pct: float | None = None
    is_all_time_low: bool = False
    first_observed: date | None = None

    @property
    def has_baseline(self) -> bool:
        return self.observations >= MIN_MEANINGFUL_OBSERVATIONS

    @property
    def direction(self) -> str:
        if self.delta_vs_previous is None or abs(self.delta_vs_previous) < 1:
            return "flat"
        return "down" if self.delta_vs_previous < 0 else "up"

    def headline(self, currency_symbol: str = "$") -> str:
        price = f"{currency_symbol}{self.current:,.0f}"
        if not self.has_baseline:
            return f"{price} ({self.observations} observation(s) so far)"
        if self.is_all_time_low:
            return f"{price} — lowest we've tracked"
        return f"{price} — {self.verdict} (bottom {self.percentile:.0f}% of {self.observations})"


def percentile_rank(values: list[float], target: float) -> float:
    """Percent of observations at or below `target`. Lower is a better deal."""
    if not values:
        return 100.0
    at_or_below = sum(1 for v in values if v <= target)
    return at_or_below / len(values) * 100.0


def _verdict(percentile: float, is_low: bool) -> str:
    if is_low:
        return VERDICTS["all_time_low"]
    if percentile <= 10:
        return VERDICTS["excellent"]
    if percentile <= 25:
        return VERDICTS["good"]
    if percentile <= 60:
        return VERDICTS["typical"]
    if percentile <= 85:
        return VERDICTS["high"]
    return VERDICTS["very_high"]


def assess(
    history: list[HistoryRow],
    *,
    field: str = "total",
    recent_days: int = 30,
    today: date | None = None,
) -> PriceAssessment | None:
    """Assess the most recent observation against everything before it."""
    series = [r for r in history if getattr(r, field, 0) > 0]
    if not series:
        return None

    today = today or series[-1].captured_on
    values = [getattr(r, field) for r in series]
    current = values[-1]
    prior = values[:-1]

    if not prior:
        return PriceAssessment(
            current=current,
            observations=1,
            verdict=VERDICTS["building"],
            all_time_low=current,
            all_time_high=current,
            median=current,
            first_observed=series[0].captured_on,
        )

    cutoff = today - timedelta(days=recent_days)
    recent = [getattr(r, field) for r in series if r.captured_on >= cutoff]

    is_low = current <= min(prior)
    rank = percentile_rank(prior, current)
    recent_median = statistics.median(recent) if recent else None
    delta_recent_pct = (
        (current - recent_median) / recent_median * 100.0
        if recent_median
        else None
    )

    observations = len(values)
    verdict = (
        VERDICTS["building"]
        if observations < MIN_MEANINGFUL_OBSERVATIONS
        else _verdict(rank, is_low)
    )

    return PriceAssessment(
        current=current,
        observations=observations,
        verdict=verdict,
        percentile=rank,
        all_time_low=min(values),
        all_time_high=max(values),
        median=statistics.median(values),
        recent_median=recent_median,
        previous=prior[-1],
        delta_vs_previous=current - prior[-1],
        delta_vs_recent_median_pct=delta_recent_pct,
        is_all_time_low=is_low and observations >= MIN_MEANINGFUL_OBSERVATIONS,
        first_observed=series[0].captured_on,
    )


def best_ever(history: list[HistoryRow], field: str = "total") -> HistoryRow | None:
    series = [r for r in history if getattr(r, field, 0) > 0]
    return min(series, key=lambda r: getattr(r, field)) if series else None


def daily_series(history: list[HistoryRow], field: str = "total") -> list[tuple[date, float]]:
    """One point per calendar day (the cheapest that day), oldest first."""
    by_day: dict[date, float] = {}
    for row in history:
        value = getattr(row, field, 0)
        if value <= 0:
            continue
        day = row.captured_on
        by_day[day] = min(by_day.get(day, value), value)
    return sorted(by_day.items())
