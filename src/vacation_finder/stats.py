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


def comparable_rows(history: list[HistoryRow], field: str) -> list[HistoryRow]:
    """The rows worth comparing on `field`.

    A trip total is only comparable when it is complete. A day priced with no
    hotel looks like a spectacular deal — Nashville at $157 of fuel — and
    counting it would make every honest price after it look terrible and stop
    the deal alerts from ever firing.
    """
    return [
        r
        for r in history
        if getattr(r, field, 0) > 0 and (field != "total" or r.is_complete)
    ]


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
    series = comparable_rows(history, field)
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
    series = comparable_rows(history, field)
    return min(series, key=lambda r: getattr(r, field)) if series else None


def daily_series(history: list[HistoryRow], field: str = "total") -> list[tuple[date, float]]:
    """One point per calendar day (the cheapest that day), oldest first."""
    by_day: dict[date, float] = {}
    for row in comparable_rows(history, field):
        value = getattr(row, field)
        day = row.captured_on
        by_day[day] = min(by_day.get(day, value), value)
    return sorted(by_day.items())


# --------------------------------------------------------------------------- #
# Direction of travel
# --------------------------------------------------------------------------- #

# Below this many days of data a slope is noise, not a trend.
MIN_TREND_DAYS = 7
# A weekly move smaller than this share of the price is "steady".
STEADY_PCT_PER_WEEK = 1.0


@dataclass(frozen=True)
class Trend:
    """Which way a price is moving, fitted over the recent window."""

    direction: str             # "rising", "falling", "steady"
    per_week: float            # currency per week (negative = falling)
    pct_per_week: float
    days: int                  # span of data the fit covers
    points: int

    def phrase(self, currency_symbol: str = "$") -> str:
        if self.direction == "steady":
            return f"steady over {self.days} days"
        word = "up" if self.per_week > 0 else "down"
        return (
            f"{word} about {currency_symbol}{abs(self.per_week):,.0f}/week "
            f"({abs(self.pct_per_week):.1f}%) over {self.days} days"
        )


def trend(series: list[tuple[date, float]], window_days: int = 28) -> Trend | None:
    """Least-squares slope of the last `window_days` of a daily series.

    A straight-line fit, not a forecast: it says how the price has been
    moving, which is the honest part. Returns None until there are at least
    MIN_TREND_DAYS days of data, because a two-day "trend" is just noise.
    """
    if not series:
        return None
    cutoff = series[-1][0] - timedelta(days=window_days)
    points = [(d, v) for d, v in series if d >= cutoff and v > 0]
    if len(points) < 3:
        return None
    span = (points[-1][0] - points[0][0]).days
    if span < MIN_TREND_DAYS - 1:
        return None

    xs = [(d - points[0][0]).days for d, _ in points]
    ys = [v for _, v in points]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x
    per_week = slope * 7
    pct = per_week / mean_y * 100 if mean_y else 0.0
    if abs(pct) < STEADY_PCT_PER_WEEK:
        direction = "steady"
    else:
        direction = "rising" if per_week > 0 else "falling"
    return Trend(direction, per_week, pct, span + 1, len(points))
