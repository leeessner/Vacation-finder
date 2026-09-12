from datetime import date

from vacation_finder.config import DateWindow, Settings, Trip
from vacation_finder.dates import candidate_windows, eligible_departures, sample

SETTINGS = Settings(lookahead_min_days=14, max_date_samples_per_trip=6)


def make_trip(**window_kwargs) -> Trip:
    return Trip(id="t", name="t", dates=DateWindow(**window_kwargs), origins=("ORD",))


def test_sample_keeps_endpoints_and_respects_limit():
    values = list(range(20))
    picked = sample(values, 5)
    assert len(picked) == 5
    assert picked[0] == 0 and picked[-1] == 19


def test_sample_returns_everything_when_under_limit():
    assert sample([1, 2, 3], 6) == [1, 2, 3]


def test_sample_is_deterministic():
    values = list(range(31))
    assert sample(values, 6) == sample(values, 6)


def test_lookahead_excludes_imminent_departures():
    trip = make_trip(
        mode="window", nights=7, earliest=date(2026, 1, 1), latest=date(2026, 2, 1)
    )
    departures = eligible_departures(trip, date(2026, 1, 1), SETTINGS)
    assert min(departures) == date(2026, 1, 15)


def test_trip_must_end_by_latest():
    trip = make_trip(
        mode="window", nights=7, earliest=date(2026, 6, 1), latest=date(2026, 6, 30)
    )
    windows = candidate_windows(trip, date(2026, 1, 1), SETTINGS)
    assert all(ret <= date(2026, 6, 30) for _, ret in windows)
    assert max(depart for depart, _ in windows) == date(2026, 6, 23)


def test_departure_weekday_filter():
    trip = make_trip(
        mode="window",
        nights=7,
        earliest=date(2026, 6, 1),
        latest=date(2026, 8, 1),
        departure_weekdays=(5,),
    )
    departures = eligible_departures(trip, date(2026, 1, 1), SETTINGS)
    assert departures and all(d.weekday() == 5 for d in departures)


def test_fixed_dates_produce_exactly_one_window():
    trip = make_trip(
        mode="fixed", nights=5, depart=date(2026, 9, 1), return_=date(2026, 9, 6)
    )
    assert candidate_windows(trip, date(2026, 1, 1), SETTINGS) == [
        (date(2026, 9, 1), date(2026, 9, 6))
    ]


def test_past_window_yields_nothing():
    trip = make_trip(
        mode="window", nights=7, earliest=date(2020, 1, 1), latest=date(2020, 2, 1)
    )
    assert candidate_windows(trip, date(2026, 1, 1), SETTINGS) == []


def test_sample_never_exceeds_the_configured_budget():
    trip = make_trip(
        mode="window", nights=3, earliest=date(2026, 6, 1), latest=date(2027, 6, 1)
    )
    assert len(candidate_windows(trip, date(2026, 1, 1), SETTINGS)) <= 6
