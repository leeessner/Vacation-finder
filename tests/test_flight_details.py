"""Flight details: segments, connections, travel time, and the fare's basis."""

from datetime import date

from vacation_finder import dashboard, flight_details
from vacation_finder.config import DateWindow, FlightPrefs

from test_pricing import make_trip

SERP_OPTION = {
    "price": 7496,
    "flights": [
        {"departure_airport": {"id": "STL", "name": "St. Louis Lambert", "time": "2027-06-04 15:10"},
         "arrival_airport": {"id": "ORD", "name": "Chicago O'Hare", "time": "2027-06-04 16:35"},
         "duration": 85, "airline": "American", "flight_number": "AA 1234"},
        {"departure_airport": {"id": "ORD", "name": "Chicago O'Hare", "time": "2027-06-04 18:05"},
         "arrival_airport": {"id": "BCN", "name": "Barcelona El Prat", "time": "2027-06-05 10:05"},
         "duration": 540, "airline": "American", "flight_number": "AA 120"},
    ],
}


def test_serpapi_option_reads_as_segments_with_a_connection():
    legs = flight_details.from_serpapi(SERP_OPTION)
    assert [(s["from"], s["to"]) for s in legs] == [("STL", "ORD"), ("ORD", "BCN")]
    assert flight_details.layovers(legs) == [("ORD", 90)]
    assert flight_details.total_minutes(legs) == 85 + 90 + 540   # not arrival minus departure
    assert flight_details.day_shift(legs) == 1                    # lands the next day
    assert flight_details.hours(715) == "11h 55m"


def test_basis_says_how_the_fare_was_estimated():
    scaled = flight_details.basis("google_flights_scaled", 6, 1, 3)
    assert "multiplied" in scaled and "6" in scaled and "bags" in scaled
    whole = flight_details.basis("google_flights", 6, 1, 3)
    assert "all 6 travellers" in whole and "3 checked bag" in whole


def test_overnight_flights_leave_the_day_before_check_in():
    trip = make_trip(flights=FlightPrefs(overnight_outbound=True))
    assert trip.flight_dates(date(2027, 6, 5), date(2027, 6, 15)) == (
        date(2027, 6, 4), date(2027, 6, 15)
    )
    assert make_trip().flight_dates(date(2027, 6, 5), date(2027, 6, 15))[0] == date(2027, 6, 5)


def test_trip_page_shows_the_flights_and_travel_time():
    info = {
        "checked_on": "2026-10-31", "price": 7496, "currency": "USD",
        "source": "google_flights_weekly", "carriers": ["American"],
        "origin": "STL", "arrive_at": "BCN", "home_from": "MAD",
        "depart_date": "2027-06-04", "return_date": "2027-06-15",
        "check_in": "2027-06-05", "check_out": "2027-06-15",
        "outbound": list(flight_details.from_serpapi(SERP_OPTION)), "inbound": [],
        "basis": "Google Flights' lowest price for all 6 travellers.",
    }
    html = dashboard._flights_section(info, None)
    assert "STL → BCN" in html and "MAD → STL" in html and "(home from MAD)" in html
    assert "Connection at ORD: 1h 30m" in html
    assert "11h 55m gate to gate" in html
    assert "Fri Jun 4, 3:10 pm" in html and "Sat Jun 5, 10:05 am" in html
    assert "full travel day" in html
    assert "How this fare was found" in html


def test_flights_over_the_time_limit_are_ignored():
    from vacation_finder.sources import google_flights

    legs = flight_details.from_serpapi(SERP_OPTION)          # 11h 55m
    assert not google_flights.too_long(legs, 20)
    assert google_flights.too_long(legs, 10)
    assert not google_flights.too_long((), 20)               # unknown time: kept


def test_weekly_check_takes_the_cheapest_fare_within_the_limit():
    from vacation_finder import insights
    from vacation_finder.config import Config, Settings

    long_way = {**SERP_OPTION, "price": 6167, "total_duration": 33 * 60}
    short_way = {**SERP_OPTION, "price": 7400, "total_duration": 12 * 60}

    class Client:
        def search(self, params, engine="google_hotels"):
            self.params = params
            return {"price_insights": {"lowest_price": 6167},
                    "other_flights": [long_way, short_way]}

    trip = make_trip(dates=DateWindow(mode="window", nights=10,
                                      earliest=date(2027, 6, 5), latest=date(2027, 6, 30)))
    settings = Settings(home_airports=("STL",), max_flight_hours=20)
    client = Client()
    found = insights.fetch(client, trip, Config(settings=settings, trips=(trip,)), date(2026, 10, 31))
    assert found.lowest == 7400
    assert client.params["max_duration"] == 1200


def test_a_saved_fare_over_the_limit_is_redone():
    from vacation_finder import insights
    from vacation_finder.config import Config, Settings
    from vacation_finder.dates import candidate_windows

    trip = make_trip()
    settings = Settings(home_airports=("STL",), max_flight_hours=10, lookahead_min_days=14)
    config = Config(settings=settings, trips=(trip,))
    today = date(2026, 10, 31)
    depart, ret = trip.flight_dates(*candidate_windows(trip, today, settings)[0])
    saved = insights.FlightInsight(
        trip_id=trip.id, checked_on=today, depart=depart, return_=ret, lowest=6000.0,
        level="", typical_low=None, typical_high=None,
        outbound=flight_details.from_serpapi(SERP_OPTION),   # 11h 55m > 10h
    )
    assert insights.due(config, {trip.id: saved}, today) == [trip]
