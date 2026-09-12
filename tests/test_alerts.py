from datetime import date, datetime, timedelta, timezone

from vacation_finder.alerts import evaluate, filter_new
from vacation_finder.config import AlertRules, Trip
from vacation_finder.stats import assess
from vacation_finder.storage import HistoryRow


def row(total: float, flight: float = 1200.0, days_ago: int = 0) -> HistoryRow:
    return HistoryRow(
        captured_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        trip_id="t",
        destination="CUN",
        depart_date=date(2027, 2, 1),
        return_date=date(2027, 2, 8),
        nights=7,
        currency="USD",
        flight_total=flight,
        flight_source="test",
        flight_carriers="AA",
        lodging_name="Hotel",
        lodging_total=total - flight,
        lodging_per_night=(total - flight) / 7,
        lodging_rating=4.0,
        ground_total=0.0,
        total=total,
        is_complete=True,
    )


def trip(**rules) -> Trip:
    return Trip(id="t", name="Test trip", alerts=AlertRules(**rules))


def history(totals: list[float]) -> list[HistoryRow]:
    n = len(totals)
    return [row(t, days_ago=n - i) for i, t in enumerate(totals)]


def test_absolute_target_fires():
    rows = history([5000, 4900, 5100, 4950, 3900])
    alerts = evaluate(trip(total_below=4000), rows[-1], assess(rows))
    assert any(a.kind == "below_target" for a in alerts)


def test_absolute_target_silent_above_threshold():
    rows = history([5000, 4900, 5100, 4950, 4800])
    alerts = evaluate(trip(total_below=4000), rows[-1], assess(rows))
    assert not any(a.kind == "below_target" for a in alerts)


def test_all_time_low_needs_minimum_observations():
    rows = history([5000, 3000])
    alerts = evaluate(trip(min_observations=5), rows[-1], assess(rows))
    assert not any(a.kind == "all_time_low" for a in alerts)


def test_all_time_low_fires_once_history_is_long_enough():
    rows = history([5000, 4900, 5100, 4950, 5050, 3000])
    alerts = evaluate(trip(min_observations=5), rows[-1], assess(rows))
    assert any(a.kind == "all_time_low" for a in alerts)


def test_percentage_drop_against_recent_median():
    rows = history([5000, 5000, 5000, 5000, 5000, 4000])
    alerts = evaluate(trip(drop_pct_vs_median=15, all_time_low=False), rows[-1], assess(rows))
    assert any(a.kind == "price_drop" for a in alerts)


def test_flight_only_threshold_is_independent_of_total():
    rows = history([9000, 9000, 9000, 9000, 9000])
    rows[-1] = row(9000, flight=800)
    alerts = evaluate(trip(flight_below=1000, total_below=None), rows[-1], assess(rows))
    kinds = {a.kind for a in alerts}
    assert "flight_below_target" in kinds
    assert "below_target" not in kinds


def test_cooldown_suppresses_a_repeat_alert():
    rows = history([5000, 4900, 5100, 4950, 3900])
    trips = {"t": trip(total_below=4000, cooldown_days=3)}
    alerts = evaluate(trips["t"], rows[-1], assess(rows))

    today = date(2026, 5, 1)
    first, state = filter_new(alerts, trips, today=today, state={})
    assert first

    again, _ = filter_new(alerts, trips, today=today + timedelta(days=1), state=state)
    assert not again


def test_cooldown_yields_when_the_price_falls_further():
    trips = {"t": trip(total_below=4000, cooldown_days=5)}
    today = date(2026, 5, 1)

    rows = history([5000, 4900, 5100, 4950, 3900])
    _, state = filter_new(evaluate(trips["t"], rows[-1], assess(rows)), trips, today=today, state={})

    cheaper = history([5000, 4900, 5100, 4950, 3900, 3400])
    fired, _ = filter_new(
        evaluate(trips["t"], cheaper[-1], assess(cheaper)),
        trips,
        today=today + timedelta(days=1),
        state=state,
    )
    assert fired, "a materially lower price should break through the cooldown"


def test_cooldown_expires_after_its_window():
    rows = history([5000, 4900, 5100, 4950, 3900])
    trips = {"t": trip(total_below=4000, cooldown_days=3)}
    alerts = evaluate(trips["t"], rows[-1], assess(rows))
    today = date(2026, 5, 1)
    _, state = filter_new(alerts, trips, today=today, state={})
    later, _ = filter_new(alerts, trips, today=today + timedelta(days=4), state=state)
    assert later
