"""House rules (distance, preferred brand, drive-not-fly), trends, the outlook,
the weekly flight insight and the prices-over-time spreadsheet."""

import csv
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from vacation_finder import insights as insights_mod
from vacation_finder.config import (
    Config,
    ConfigError,
    Destination,
    FlightPrefs,
    LodgingPrefs,
    load_config,
)
from vacation_finder.forecast import BOOK, SOON, WAIT, WATCH, outlook
from vacation_finder.insights import FlightInsight
from vacation_finder.lodging import LodgingBook, slot_cost
from vacation_finder.pricing import TripPricer
from vacation_finder.sources.google_hotels import search_lodging
from vacation_finder.stats import trend
from vacation_finder.storage import HistoryRow
from vacation_finder.suggest import CatalogEntry, Suggestion, as_trip_yaml

from test_pricing import SETTINGS, FakeHotels, FakeTravelpayouts, fare, make_trip

TODAY = date(2026, 10, 25)
DOWNTOWN = (36.1627, -86.7816)


def prop(name, stay, miles=0.5, rating=4.3, stars=3):
    """A Google Hotels property `miles` due north of downtown Nashville."""
    return {
        "type": "hotel", "name": name, "overall_rating": rating,
        "extracted_hotel_class": stars, "total_rate": {"extracted_lowest": stay},
        "gps_coordinates": {"latitude": DOWNTOWN[0] + miles / 69.0, "longitude": DOWNTOWN[1]},
    }


NASHVILLE = Destination(
    code="BNA", label="Nashville, TN", hotel_query="Downtown Nashville, TN",
    center=DOWNTOWN, within_miles=3,
)


def nashville_trip(prefer=("Embassy Suites",)):
    return make_trip(
        destinations=(NASHVILLE,),
        flights=FlightPrefs(enabled=False),
        travel_mode="drive",
        lodging=LodgingPrefs(rooms=1, min_stars=3, min_rating=4.0, prefer=prefer),
    )


# --------------------------------------------------------------------------- #
# Distance from the center
# --------------------------------------------------------------------------- #

def test_hotels_beyond_the_radius_are_ignored_however_cheap():
    quote = search_lodging(
        FakeHotels([prop("Far Cheap Inn", 600, miles=9), prop("Downtown Suites", 1400, miles=0.8)]),
        nashville_trip(), "Downtown Nashville, TN", date(2027, 3, 12), date(2027, 3, 16),
        "USD", SETTINGS, center=DOWNTOWN, within_miles=3,
    )
    assert quote.property_name == "Downtown Suites"
    assert quote.distance_miles == pytest.approx(0.8, abs=0.05)


def test_a_property_without_coordinates_fails_a_distance_rule():
    no_gps = prop("Mystery", 500)
    del no_gps["gps_coordinates"]
    assert search_lodging(
        FakeHotels([no_gps]), nashville_trip(), "x", date(2027, 3, 12), date(2027, 3, 16),
        "USD", SETTINGS, center=DOWNTOWN, within_miles=3,
    ) is None


def test_guest_rating_below_four_is_excluded():
    quote = search_lodging(
        FakeHotels([prop("Low Rated", 500, rating=3.6), prop("Well Rated", 900, rating=4.1)]),
        nashville_trip(), "x", date(2027, 3, 12), date(2027, 3, 16), "USD", SETTINGS,
    )
    assert quote.property_name == "Well Rated"


def test_one_room_for_six_asks_google_for_all_six_in_one_room():
    hotels = FakeHotels([prop("Suites", 900)])
    search_lodging(hotels, nashville_trip(), "x", date(2027, 3, 12), date(2027, 3, 16), "USD", SETTINGS)
    assert hotels.calls[0]["adults"] == 2
    assert hotels.calls[0]["children"] == 4


# --------------------------------------------------------------------------- #
# Preferred brand
# --------------------------------------------------------------------------- #

class ByQuery(FakeHotels):
    """Different results for the general search and the brand search."""

    def __init__(self, general, brand):
        super().__init__()
        self.general, self.brand = general, brand

    def search(self, params):
        self.calls.append(params)
        return {"properties": self.brand if "Embassy" in params["q"] else self.general}


def price_nashville(hotels, trip=None):
    trip = trip or nashville_trip()
    config = Config(settings=SETTINGS, trips=(trip,))
    return TripPricer(config, client=FakeTravelpayouts(), hotels=hotels).price_trip(
        trip, today=TODAY
    )


def test_preferred_brand_is_tracked_with_the_cheaper_alternative_noted():
    hotels = ByQuery(
        general=[prop("Homewood Suites Downtown", 1400)],
        brand=[prop("Embassy Suites by Hilton Nashville at Vanderbilt", 1176, miles=1.1),
               prop("Embassy Suites by Hilton Nashville Airport", 1170, miles=7)],
    )
    quote = price_nashville(hotels).quotes[0]
    assert quote.lodging.property_name == "Embassy Suites by Hilton Nashville at Vanderbilt"
    assert quote.lodging.total_price == 1176       # the airport one is 7 mi out
    assert any("$224 less than the cheapest" in n for n in quote.notes)
    assert any(c["q"] == "Embassy Suites Nashville, TN" for c in hotels.calls)


def test_no_qualifying_preferred_brand_falls_back_to_the_cheapest_with_a_note():
    hotels = ByQuery(general=[prop("Homewood Suites Downtown", 1400)], brand=[])
    quote = price_nashville(hotels).quotes[0]
    assert quote.lodging.property_name == "Homewood Suites Downtown"
    assert any("no Embassy Suites within 3 mi" in n for n in quote.notes)


def test_no_note_when_the_cheapest_is_already_the_preferred_brand():
    embassy = prop("Embassy Suites by Hilton Nashville Downtown", 1300)
    quote = price_nashville(ByQuery(general=[embassy], brand=[embassy])).quotes[0]
    assert quote.lodging.property_name.startswith("Embassy Suites")
    assert not [n for n in quote.notes if "Embassy" in n]


def test_a_preferred_brand_slot_costs_an_extra_search():
    assert slot_cost(nashville_trip()) == 2
    assert slot_cost(nashville_trip(prefer=())) == 1


def test_planner_never_skips_the_stalest_slot_for_a_cheaper_one():
    trip = nashville_trip()
    config = Config(settings=SETTINGS, trips=(trip,))
    book = LodgingBook.plan(config, TODAY, allowance=1)
    assert book.refresh == set()      # the first slot costs 2; it waits for tomorrow


# --------------------------------------------------------------------------- #
# Trend and outlook
# --------------------------------------------------------------------------- #

def series(start, per_day, days=21):
    d0 = date(2026, 10, 1)
    return [(d0 + timedelta(i), start + per_day * i) for i in range(days)]


def test_trend_reports_direction_and_weekly_rate():
    rising = trend(series(1000, 10))
    assert rising.direction == "rising"
    assert rising.per_week == pytest.approx(70)
    assert trend(series(1000, 0)).direction == "steady"
    assert trend(series(1000, -10)).direction == "falling"


def test_no_trend_from_a_few_days():
    assert trend(series(1000, 10, days=4)) is None


def europe_trip():
    return make_trip()          # flights on, June 2027


def insight(level="typical", low=7800.0, high=9400.0):
    return FlightInsight("t", TODAY, date(2027, 6, 5), date(2027, 6, 15), 7962.0, level, low, high)


def test_low_fare_says_book():
    o = outlook(europe_trip(), SETTINGS, TODAY, date(2027, 6, 5),
                flight_price=7400, insight=insight(), days_of_history=30)
    assert o.label == BOOK


def test_far_out_typical_fare_says_watch_and_when_the_window_opens():
    o = outlook(europe_trip(), SETTINGS, TODAY, date(2027, 6, 5),
                flight_price=8500, insight=insight(), days_of_history=30)
    assert o.label == WATCH
    assert "Nov 2026" in o.summary


def test_inside_five_months_says_book_soon():
    o = outlook(europe_trip(), SETTINGS, date(2027, 2, 1), date(2027, 6, 5),
                flight_price=8500, insight=insight(), days_of_history=60)
    assert o.label == SOON


def test_high_fare_mid_window_says_wait():
    o = outlook(europe_trip(), SETTINGS, date(2026, 12, 15), date(2027, 6, 5),
                flight_price=9900, insight=insight(), days_of_history=60)
    assert o.label == WAIT


def test_fast_climb_far_out_says_book_soon():
    o = outlook(europe_trip(), SETTINGS, TODAY, date(2027, 6, 5), flight_price=8500,
                insight=insight(), flight_trend=trend(series(8000, 40)), days_of_history=21)
    assert o.label == SOON


def test_confidence_grows_with_evidence():
    thin = outlook(europe_trip(), SETTINGS, TODAY, date(2027, 6, 5), days_of_history=3)
    rich = outlook(europe_trip(), SETTINGS, TODAY, date(2027, 6, 5),
                   insight=insight(), days_of_history=45)
    assert thin.confidence == "low"
    assert "only 3 day(s) of tracking" in thin.summary
    assert rich.confidence == "high"


def test_drive_trip_outlook_is_about_hotels():
    o = outlook(nashville_trip(), SETTINGS, TODAY, date(2027, 3, 12),
                lodging_trend=trend(series(1000, 10)), days_of_history=21)
    assert o.label == SOON
    assert "refundable" in o.summary


# --------------------------------------------------------------------------- #
# Weekly Google Flights insight
# --------------------------------------------------------------------------- #

class FakeFlights:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def search(self, params, engine="google_hotels"):
        self.calls.append((engine, params))
        return self.payload


def test_insight_is_parsed_from_google_flights():
    client = FakeFlights({
        "price_insights": {"lowest_price": 7962, "price_level": "typical",
                           "typical_price_range": [7800, 9400]},
    })
    trip = europe_trip()
    got = insights_mod.fetch(client, trip, Config(settings=SETTINGS, trips=(trip,)), TODAY)
    engine, params = client.calls[0]
    assert engine == "google_flights"
    assert params["adults"] == 2 and params["children"] == 4
    assert (got.lowest, got.level, got.typical_low, got.typical_high) == (7962, "typical", 7800, 9400)


def test_insight_falls_back_to_the_cheapest_listed_fare():
    client = FakeFlights({"best_flights": [{"price": 8481}], "other_flights": [{"price": 8100}]})
    trip = europe_trip()
    got = insights_mod.fetch(client, trip, Config(settings=SETTINGS, trips=(trip,)), TODAY)
    assert got.lowest == 8100 and got.level == ""


def test_insights_are_due_weekly_oldest_first():
    a, b = make_trip(id="a"), make_trip(id="b")
    config = Config(settings=SETTINGS, trips=(a, b, nashville_trip()))
    known = {"a": replace(insight(), trip_id="a", checked_on=TODAY - timedelta(days=8)),
             "b": replace(insight(), trip_id="b", checked_on=TODAY - timedelta(days=2))}
    assert [t.id for t in insights_mod.due(config, known, TODAY)] == ["a"]
    assert [t.id for t in insights_mod.due(config, {}, TODAY)] == ["a", "b"]   # drive trip skipped


def test_scraper_failure_falls_back_to_the_weekly_fare_for_the_same_dates():
    trip = europe_trip()
    config = Config(settings=SETTINGS, trips=(trip,))
    weekly = {trip.id: replace(insight(), trip_id=trip.id)}
    result = TripPricer(
        config, client=FakeTravelpayouts(), hotels=FakeHotels([prop("Inn", 2000)]), insights=weekly
    ).price_trip(trip, today=date(2026, 9, 12))
    first = [q for q in result.quotes if q.depart_date == date(2027, 6, 5)][0]
    assert first.flight.total_price == 7962
    assert first.flight.source == "google_flights_weekly"


# --------------------------------------------------------------------------- #
# Spreadsheet
# --------------------------------------------------------------------------- #

def history_row(day, flight, lodging, complete=True):
    return HistoryRow(
        captured_at=datetime(2026, 10, day, 14, tzinfo=timezone.utc), trip_id="t",
        destination="BCN", depart_date=date(2027, 6, 5), return_date=date(2027, 6, 15),
        nights=10, currency="USD", flight_total=flight, flight_source="", flight_carriers="",
        lodging_name="Inn", lodging_total=lodging, lodging_per_night=lodging / 10,
        lodging_rating=4.2, ground_total=500.0, total=flight + lodging + 500,
        is_complete=complete,
    )


def test_spreadsheet_has_a_row_per_day_and_three_columns_per_trip(tmp_path, monkeypatch):
    from vacation_finder import summary

    rows = [history_row(6, 0, 0, complete=False), history_row(20, 8000, 4000),
            history_row(21, 8100, 0, complete=False)]
    monkeypatch.setattr(summary, "load_history", lambda trip_id: rows)
    trip = europe_trip()
    path = summary.write_summary(Config(settings=SETTINGS, trips=(trip,)), tmp_path / "p.csv")
    lines = list(csv.reader(path.open()))
    assert lines[0] == ["date", "Test trip — flights", "Test trip — hotel", "Test trip — total"]
    assert lines[1] == ["2026-10-20", "8000", "4000", "12500"]
    assert lines[2] == ["2026-10-21", "8100", "", ""]     # no hotel: no total
    assert len(lines) == 3                                  # the empty Oct 6 is skipped


# --------------------------------------------------------------------------- #
# Config and suggestions
# --------------------------------------------------------------------------- #

def write(tmp_path, body):
    path = tmp_path / "trips.yaml"
    path.write_text("settings:\n  home_airports: [STL]\n" + body)
    return path


def test_center_and_rules_parse(tmp_path):
    path = write(tmp_path,
        "trips:\n  - id: a\n    name: A\n"
        "    destinations:\n      - {code: BNA, center: [36.16, -86.78], within_miles: 3, drive_hours: 4.5}\n"
        "    lodging: {rooms: 1, min_rating: 4.0, prefer: [Embassy Suites]}\n"
        "    dates: {mode: window, earliest: 2027-03-12, latest: 2027-03-19, nights: 4}\n")
    trip = load_config(path).trips[0]
    assert trip.destinations[0].center == (36.16, -86.78)
    assert trip.destinations[0].within_miles == 3
    assert trip.lodging.prefer == ("Embassy Suites",)


def test_bad_center_is_a_config_error(tmp_path):
    path = write(tmp_path,
        "trips:\n  - id: a\n    name: A\n    destinations:\n      - {code: BNA, center: [999, 1]}\n"
        "    dates: {mode: window, earliest: 2027-03-12, latest: 2027-03-19, nights: 4}\n")
    with pytest.raises(ConfigError, match="center"):
        load_config(path)


def test_old_search_budget_key_still_works(tmp_path):
    path = write(tmp_path, "  hotel_searches_per_run: 5\ntrips: []\n")
    assert load_config(path).settings.searches_per_run == 5


def suggestion(drive_hours, region="USA"):
    entry = CatalogEntry(code="IND", label="Indianapolis, Indiana", region=region,
                         drive_hours=drive_hours, tags=("city",))
    return Suggestion(entry=entry, score=1.0)


def test_a_nearby_suggestion_is_a_drive_with_one_room():
    text = as_trip_yaml(suggestion(4), nashville_trip())
    assert "travel: drive" in text
    assert "rooms: 1" in text and "min_rating: 4.0" in text


def test_a_far_suggestion_is_a_flight():
    assert "travel: drive" not in as_trip_yaml(suggestion(9), nashville_trip())
