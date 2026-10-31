"""Travel legs and timings: dates from the stay, airport legs from the flights."""

from datetime import date, datetime
from pathlib import Path

import pytest

from vacation_finder import dashboard, journey

CHECK_IN = date(2027, 7, 8)          # Day 1


def test_a_train_on_day_7_lands_on_the_right_date_and_times():
    leg = journey.Leg(day=7, mode="train", origin="Paris Gare de l'Est", dest="Strasbourg",
                      minutes=106, depart="09:25")
    start, end, how = journey.leg_times(leg, CHECK_IN, None, None)
    assert journey.span(start, end) == "Wed Jul 14 9:25a – 11:11a"
    assert how == ""


def test_airport_legs_are_timed_from_the_flights():
    landed = datetime(2027, 7, 8, 16, 15)
    home = datetime(2027, 7, 17, 11, 50)
    arrive = journey.Leg(1, "taxi", "CDG", "Apartment", 55, kind="from_airport")
    leave = journey.Leg(10, "train", "Strasbourg", "CDG", 125, kind="to_airport")
    start, end, _ = journey.leg_times(arrive, CHECK_IN, landed, home)
    assert (start, end) == (datetime(2027, 7, 8, 17, 15), datetime(2027, 7, 8, 18, 10))
    start, end, how = journey.leg_times(leave, CHECK_IN, landed, home)
    assert end == datetime(2027, 7, 17, 8, 50)                # 3 h before the flight
    assert start == datetime(2027, 7, 17, 6, 45) and "3 h" in how


def test_timed_line_reads_naturally():
    item = journey.Timed(6, "Disneyland Paris rope drop", "07:45", "09:00", "RER A from Châtelet")
    assert journey.timed_line(item, CHECK_IN) == (
        "Tue Jul 13: Leave 7:45a (RER A from Châtelet) · arrive 9:00a · Disneyland Paris rope drop"
    )


def test_bad_entries_are_reported(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("legs:\n  - {day: 7, mode: rocket, from: A, to: B, minutes: 5}\n"
                    "timed:\n  - {day: 2, what: Louvre, leave: '9am'}\n")
    assert journey.load(path) == ([], [])
    with pytest.raises(journey.JourneyError):
        journey.load(path, strict=True)


def test_every_trip_plan_has_valid_travel():
    plans = [p for p in Path("plans").glob("*.md")
             if p.name not in dashboard.NOT_PLANS | {"resorts.md"}]
    assert plans
    for plan in plans:
        legs, timed = journey.load(journey.travel_path(plan), strict=True)
        assert legs, f"no travel legs for {plan.name}"


def test_timings_land_under_their_day():
    html = ('<p class="day"><strong>Day 6 — Disneyland Paris.</strong> The one day.</p>'
            '<p class="day"><strong>Days 7–10 — Resort.</strong></p>')
    timed = (journey.Timed(6, "Rope drop", "07:45", "09:00", "RER A"),)
    out = dashboard._with_timings(html, timed, CHECK_IN)
    assert '</strong><span class=\'timing\'>Tue Jul 13: Leave 7:45a (RER A)' in out
    assert out.count("timing") == 1
