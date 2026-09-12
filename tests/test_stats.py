from datetime import date, datetime, timedelta, timezone

from vacation_finder.stats import MIN_MEANINGFUL_OBSERVATIONS, assess, percentile_rank
from vacation_finder.storage import HistoryRow


def row(total: float, days_ago: int, flight: float = 0.0) -> HistoryRow:
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return HistoryRow(
        captured_at=stamp,
        trip_id="t",
        destination="CUN",
        depart_date=date(2027, 2, 1),
        return_date=date(2027, 2, 8),
        nights=7,
        currency="USD",
        flight_total=flight or total * 0.4,
        flight_source="test",
        flight_carriers="AA",
        lodging_name="Hotel",
        lodging_total=total * 0.6,
        lodging_per_night=total * 0.6 / 7,
        lodging_rating=4.0,
        ground_total=0.0,
        total=total,
        is_complete=True,
    )


def series(totals: list[float]) -> list[HistoryRow]:
    n = len(totals)
    return [row(t, days_ago=n - i) for i, t in enumerate(totals)]


def test_percentile_rank_bounds():
    assert percentile_rank([100, 200, 300], 50) == 0.0
    assert percentile_rank([100, 200, 300], 300) == 100.0
    assert percentile_rank([100, 200, 300], 200) > 60


def test_empty_history_has_no_assessment():
    assert assess([]) is None


def test_single_observation_reports_building_baseline():
    result = assess(series([3000]))
    assert result.observations == 1
    assert result.verdict == "building baseline"
    assert not result.is_all_time_low


def test_short_history_stays_in_building_baseline():
    result = assess(series([3000, 2900, 2800]))
    assert result.observations < MIN_MEANINGFUL_OBSERVATIONS
    assert result.verdict == "building baseline"


def test_new_low_with_enough_history_is_flagged():
    result = assess(series([4000, 3900, 4100, 3950, 4050, 3400]))
    assert result.is_all_time_low
    assert result.verdict == "all-time low"
    assert result.all_time_low == 3400


def test_expensive_observation_is_not_a_low():
    result = assess(series([3000, 3100, 2900, 3050, 2950, 4200]))
    assert not result.is_all_time_low
    assert result.verdict in {"high", "very high"}
    assert result.percentile > 85


def test_delta_versus_previous_tracks_direction():
    result = assess(series([3000, 3100, 2900, 3050, 2950, 2800]))
    assert result.previous == 2950
    assert result.delta_vs_previous == -150
    assert result.direction == "down"


def test_zero_rows_are_excluded_from_the_series():
    """A failed lookup writes 0 and must not become an all-time low."""
    rows = series([3000, 3100, 2900, 3050, 2950])
    rows.append(row(0.0, days_ago=0))
    result = assess(rows)
    assert result.current == 2950
    assert result.all_time_low == 2900


def test_recent_median_uses_only_the_recent_window():
    old = [row(9000, days_ago=200 - i) for i in range(5)]
    recent = [row(3000, days_ago=5 - i) for i in range(5)]
    result = assess(old + recent, recent_days=30)
    assert result.recent_median == 3000
    assert result.median > 3000


def test_assess_can_target_the_flight_column():
    rows = series([4000, 3900, 4100, 3950, 4050, 3800])
    result = assess(rows, field="flight_total")
    assert result.current == rows[-1].flight_total
