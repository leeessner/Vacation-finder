"""Flight details: segments, connections, travel time, and the fare's basis."""

from datetime import date

from vacation_finder import dashboard, flight_details
from vacation_finder.config import FlightPrefs

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
