"""The dashboard: a highlights home page, and a page per trip with its plan."""

from datetime import date, datetime, timezone

import pytest

from vacation_finder import dashboard
from vacation_finder.forecast import Outlook
from vacation_finder.report import TripReport
from vacation_finder.storage import HistoryRow

from test_pricing import make_trip

PLAN = """# Paris & Alsace — draft plan (summer 2027)

**Shape:** 9 nights. Six in Paris.

## Day by day

**Day 1 — Arrive.** Sailboats in the Luxembourg Gardens.

| Item | € |
|---|---:|
| Louvre | 64 |

Sainte-Chapelle ~€20 (*check*). [Louvre](https://www.louvre.fr)
"""


def row(day: int, flights: float, hotel: float) -> HistoryRow:
    return HistoryRow(
        captured_at=datetime(2026, 10, day, tzinfo=timezone.utc),
        trip_id="paris", destination="Paris", depart_date=date(2027, 6, 5),
        return_date=date(2027, 6, 14), nights=9, currency="USD",
        flight_total=flights, flight_source="google_flights", flight_carriers="AF",
        lodging_name="Apartment", lodging_total=hotel, lodging_per_night=hotel / 9,
        lodging_rating=4.6, ground_total=0.0, total=flights + hotel, is_complete=True,
    )


def priced_report(**trip) -> TripReport:
    rows = [row(28, 8000, 3000), row(29, 7900, 3100)]
    report = TripReport(trip=make_trip(**trip), history=rows, latest=rows[-1])
    report.flight_series = [(r.captured_on, r.flight_total) for r in rows]
    report.lodging_series = [(r.captured_on, r.lodging_total) for r in rows]
    report.series = [(r.captured_on, r.total) for r in rows]
    report.outlook = Outlook("Keep watching", ["8 months out"], "low")
    return report


def build(tmp_path, reports):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "paris-alsace.md").write_text(PLAN)
    (plans / "florence-tuscany.md").write_text(PLAN.replace("Paris & Alsace", "Florence & Tuscany")
                                               .replace("9 nights", "8 nights"))
    (plans / "resorts.md").write_text("# The family resorts\n\nA list.")
    (plans / "family-profile.md").write_text("# Profile\n")
    out = tmp_path / "docs" / "index.html"
    dashboard.build(reports, config=None, output=out, plans_dir=plans)
    return out.parent


def test_home_is_just_the_highlights(tmp_path):
    report = priced_report(id="paris", name="Paris & Alsace", plan="plans/paris-alsace.md")
    docs = build(tmp_path, [report])
    home = (docs / "index.html").read_text()

    assert 'href="trips/paris.html"' in home
    assert "$11,000" in home                          # the price
    assert "Keep watching" in home and "low confidence" in home
    assert "<svg" in home and "Hotel" in home and "Flights" in home
    assert "Total</span>" not in home                 # chart is flights and hotel only
    assert "Price tracker" not in home                # detail lives on the trip page
    assert "9 nights · $11,000" in home               # the tab


def test_trip_page_has_tracker_detail_then_plan(tmp_path):
    report = priced_report(id="paris", name="Paris & Alsace", plan="plans/paris-alsace.md")
    docs = build(tmp_path, [report])
    page = (docs / "trips" / "paris.html").read_text()

    assert page.index("Price tracker") < page.index("Day 1 — Arrive.")
    assert '<p class="day"><strong>Day 1 — Arrive.</strong>' in page
    assert '<div class="scroll"><table>' in page
    assert '<span class="check">check</span>' in page
    assert 'target="_blank"' in page
    assert 'href="../index.html"' in page


def test_untracked_plans_get_pages_but_only_trips_get_cards(tmp_path):
    docs = build(tmp_path, [])
    home = (docs / "index.html").read_text()

    assert (docs / "trips" / "plan-florence-tuscany.html").exists()
    assert (docs / "trips" / "plan-resorts.html").exists()
    assert not (docs / "trips" / "plan-family-profile.html").exists()
    assert "8 nights · not tracked yet" in home       # Tuscany tab
    assert home.count('class="tcard"') == 2           # Tuscany and Paris plans; not resorts


def test_stale_trip_pages_are_removed(tmp_path):
    docs = tmp_path / "docs" / "trips"
    docs.mkdir(parents=True)
    (docs / "old-trip.html").write_text("gone")
    build(tmp_path, [])
    assert not (docs / "old-trip.html").exists()


# --------------------------------------------------------------------------- #
# Itinerary map
# --------------------------------------------------------------------------- #

MAP = """places:
  - {name: "Apartment (stay)", kind: stay, days: [1, 2, 3], at: [48.849, 2.34], area: Paris}
  - {name: Louvre, days: [2], at: [48.8606, 2.3376], area: Paris}
  - {name: Cathedral, days: [8], at: [48.5819, 7.751], area: Alsace}
  - {name: "Rodin (option)", kind: option, days: [], at: [48.8553, 2.3159], area: Paris}
"""


def test_trip_page_has_a_map_above_day_by_day(tmp_path):
    (tmp_path / "plans" / "maps").mkdir(parents=True)
    (tmp_path / "plans" / "maps" / "paris-alsace.yaml").write_text(MAP)
    report = priced_report(id="paris", name="Paris & Alsace", plan="plans/paris-alsace.md")
    plans = tmp_path / "plans"
    (plans / "paris-alsace.md").write_text(PLAN)
    out = tmp_path / "docs" / "index.html"
    dashboard.build([report], config=None, output=out, plans_dir=plans)
    page = (out.parent / "trips" / "paris.html").read_text()

    assert "leaflet.js" in page and 'id="trip-map"' in page
    assert page.index('id="trip-map"') < page.index("<h2>Day by day</h2>")
    assert '"label": "⌂"' in page and '"label": "2"' in page
    assert 'data-area="Alsace"' in page                    # zoom to each base
    assert "<dt>Day 2</dt><dd><button" in page and "<dt>Options</dt>" in page
    assert "leaflet" not in (out.parent / "index.html").read_text()   # only trip pages


def test_a_page_without_a_map_file_has_no_map(tmp_path):
    docs = build(tmp_path, [priced_report(id="paris", name="Paris", plan="plans/paris-alsace.md")])
    assert "trip-map" not in (docs / "trips" / "paris.html").read_text()


def test_bad_map_entries_are_skipped_not_fatal(tmp_path):
    from vacation_finder import itinerary_map

    path = tmp_path / "m.yaml"
    path.write_text("places:\n  - {name: Hohensalzburg fortress, Salzburg, at: [47.79, 13.04]}\n"
                    "  - {name: Ok, at: [47.0, 13.0]}\n")
    assert [p.name for p in itinerary_map.load_places(path)] == ["Ok"]
    with pytest.raises(itinerary_map.MapError, match="quote names"):
        itinerary_map.load_places(path, strict=True)


def test_every_plan_in_the_repo_has_a_valid_map():
    from pathlib import Path

    from vacation_finder import itinerary_map

    plans = [p for p in Path("plans").glob("*.md") if p.name not in dashboard.NOT_PLANS]
    assert plans
    for plan in plans:
        places = itinerary_map.load_places(itinerary_map.map_path(plan), strict=True)
        assert places, f"no map for {plan.name}"
