"""Turning a trip's flexible date window into a bounded set of dates to price.

A six-week window with a 7-night stay has ~35 possible departure dates. Pricing
all of them every day would burn the free API quota in a week, so we sample a
deterministic, evenly-spaced subset. Determinism matters: the day-over-day price
series is only meaningful if we ask about roughly the same dates each run.
"""

from __future__ import annotations

from datetime import date, timedelta

from .config import Settings, Trip


def eligible_departures(trip: Trip, today: date, settings: Settings) -> list[date]:
    """Every departure date in the trip's window, before sampling."""
    window = trip.dates
    floor = today + timedelta(days=settings.lookahead_min_days)

    if window.mode == "fixed":
        assert window.depart is not None
        return [window.depart] if window.depart >= today else []

    assert window.earliest is not None and window.latest is not None
    start = max(window.earliest, floor)
    # The trip must end by `latest`, so the last legal departure is earlier.
    end = window.latest - timedelta(days=window.nights)

    out: list[date] = []
    day = start
    while day <= end:
        if not window.departure_weekdays or day.weekday() in window.departure_weekdays:
            out.append(day)
        day += timedelta(days=1)
    return out


def sample(candidates: list[date], limit: int) -> list[date]:
    """Evenly-spaced subset of `candidates`, always keeping first and last."""
    if limit <= 0 or len(candidates) <= limit:
        return list(candidates)
    if limit == 1:
        return [candidates[0]]
    last = len(candidates) - 1
    picks = sorted({round(i * last / (limit - 1)) for i in range(limit)})
    return [candidates[i] for i in picks]


def candidate_windows(
    trip: Trip, today: date, settings: Settings
) -> list[tuple[date, date]]:
    """The (depart, return) pairs to price for this trip on this run."""
    if trip.dates.mode == "fixed":
        assert trip.dates.depart is not None and trip.dates.return_ is not None
        departures = eligible_departures(trip, today, settings)
        return [(trip.dates.depart, trip.dates.return_)] if departures else []

    limit = trip.max_date_samples or settings.max_date_samples_per_trip
    nights = trip.dates.nights
    return [
        (d, d + timedelta(days=nights))
        for d in sample(eligible_departures(trip, today, settings), limit)
    ]
