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


def test_a_closed_day_is_flagged():
    orsay = journey.Timed(5, "Orsay opens 9:30", "09:00", "09:20", closed=("Mon",))
    assert "Monday, Jul 12" in journey.clash(orsay, CHECK_IN)        # Day 5 of a Jul 8 stay
    assert journey.clash(orsay, date(2027, 7, 9)) == ""              # a day later: Tuesday


def _report(check_in, check_out):
    from datetime import timezone

    from vacation_finder.report import TripReport
    from vacation_finder.storage import HistoryRow

    from test_pricing import make_trip

    row = HistoryRow(
        captured_at=datetime(2026, 10, 31, tzinfo=timezone.utc), trip_id="t", destination="X",
        depart_date=check_in, return_date=check_out, nights=(check_out - check_in).days,
        currency="USD", flight_total=7000, flight_source="google_flights_weekly",
        flight_carriers="", lodging_name="", lodging_total=0, lodging_per_night=0,
        lodging_rating=None, ground_total=0, total=7000, is_complete=False,
    )
    return TripReport(trip=make_trip(), latest=row)


def _seg(frm, to, dep, arr, minutes):
    return {"from": frm, "to": to, "depart": dep, "arrive": arr, "minutes": minutes}


def test_travel_lands_in_the_agenda_with_warnings():
    report = _report(date(2027, 6, 5), date(2027, 6, 15))           # Day 11 = Jun 15
    info = {
        "origin": "STL", "arrive_at": "LHR", "home_from": "LIS",
        "depart_date": "2027-06-04", "return_date": "2027-06-15",
        "outbound": [_seg("STL", "LHR", "2027-06-05T08:00", "2027-06-05T20:20", 500)],
        "inbound": [_seg("LIS", "STL", "2027-06-15T11:05", "2027-06-15T18:00", 600)],
    }
    plan = dashboard.Plan(Path("p.md"), "P", "", "", legs=(
        journey.Leg(1, "train", "Heathrow", "South Kensington", 50, kind="from_airport"),
        journey.Leg(11, "car", "Lagos", "Lisbon airport", 180, kind="to_airport"),
    ))
    rows = dashboard._journey_rows(report, plan, info)
    assert [r.day for r in rows] == [0, 1, 11, 11]                  # out, in, to the airport, home
    late, early = rows[1].warning, rows[2].warning
    assert "10:10p" in late and "Day 2" in late                      # 8:20p landing + 1 h + 50 min
    assert "5:05a" in early and "near the airport" in early          # 3 h drive, 3 h before 11:05a

    html = ('<p class="day"><strong>Day 0 — Travel day.</strong></p>'
            '<p class="day"><strong>Day 11 — Home.</strong></p>')
    out = dashboard._with_timings(html, (), date(2027, 6, 5), rows)
    assert "<b>Fly</b> STL → LHR" in out and "<b>Car</b> Lagos → Lisbon airport" in out
    assert out.count("timing clash") == 1                           # the early start; no Day 1 here


def test_a_fare_that_lands_after_day_1_is_flagged():
    report = _report(date(2027, 6, 5), date(2027, 6, 14))
    info = {"origin": "STL", "arrive_at": "BRU", "home_from": "BRU",
            "outbound": [_seg("STL", "BRU", "2027-06-04T07:00", "2027-06-06T12:55", 900)],
            "inbound": []}
    rows = dashboard._journey_rows(report, None, info)
    assert "lands Sun Jun 6, after Day 1" in rows[0].warning
