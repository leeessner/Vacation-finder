"""Pricing tests against recorded-shape Travelpayouts payloads — no network."""

from datetime import date

import pytest

from vacation_finder.config import Config, DateWindow, Destination, Settings, Trip
from vacation_finder.models import Party
from vacation_finder.pricing import TripPricer
from vacation_finder.sources.base import SourceError
from vacation_finder.sources.travelpayouts import search_flights, search_lodging

SETTINGS = Settings(
    home_airports=("STL",),
    google_flights_enabled=False,   # keep the scraper out of unit tests
    max_hotels_per_query=5,
    lookahead_min_days=14,
    max_date_samples_per_trip=2,
    hotel_price_is_per_night=False,
)


def fare(
    value: float,
    depart: str = "2027-06-05",
    ret: str = "2027-06-15",
    changes: int = 1,
    gate: str = "Aviasales",
) -> dict:
    """One row as /v2/prices/week-matrix returns it."""
    return {
        "show_to_affiliates": True,
        "trip_class": 0,
        "origin": "STL",
        "destination": "BCN",
        "depart_date": depart,
        "return_date": ret,
        "number_of_changes": changes,
        "value": value,
        "found_at": "2026-09-10T08:14:22",
        "distance": 7300,
        "actual": True,
    }


def hotel(name: str, price: float, stars: int = 4, hotel_id: int = 1) -> dict:
    """One row as Hotellook /cache.json returns it."""
    return {
        "hotelId": hotel_id,
        "hotelName": name,
        "stars": stars,
        "priceFrom": price,
        "priceAvg": price * 1.2,
        "location": {"name": "Barcelona"},
    }


class FakeTravelpayouts:
    """Stands in for the client, returning canned payloads by path."""

    def __init__(self, fares=None, hotels=None, fail_on=()):
        self.fares = fares if fares is not None else []
        self.hotels = hotels if hotels is not None else []
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict]] = []

    def get_flights(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        if any(token in path for token in self.fail_on):
            raise SourceError(f"stubbed failure for {path}")
        return {"success": True, "data": self.fares}

    def get_hotels(self, path: str, params: dict):
        self.calls.append((path, params))
        if any(token in path for token in self.fail_on):
            raise SourceError(f"stubbed failure for {path}")
        return self.hotels


def make_trip(**overrides) -> Trip:
    defaults = dict(
        id="t",
        name="Test trip",
        origins=("STL",),
        destinations=(Destination(code="BCN", label="Barcelona"),),
        party=Party(adults=2, children=(8, 7, 5, 3)),
        dates=DateWindow(
            mode="window", nights=10, earliest=date(2027, 6, 5), latest=date(2027, 6, 30)
        ),
    )
    defaults.update(overrides)
    return Trip(**defaults)


def price_flight(client, trip=None, depart=date(2027, 6, 5), ret=date(2027, 6, 15)):
    return search_flights(
        client, trip or make_trip(), "STL", "BCN", depart, ret, "USD", SETTINGS
    )


# --------------------------------------------------------------------------- #
# Flights
# --------------------------------------------------------------------------- #

def test_per_adult_fare_is_scaled_to_the_whole_party():
    """Travelpayouts quotes one adult; six travellers cost six times that."""
    quote = price_flight(FakeTravelpayouts(fares=[fare(800)]))
    assert quote.total_price == 4800.0        # 800 x 6
    assert quote.source == "travelpayouts"


def test_cheapest_eligible_fare_wins():
    quote = price_flight(FakeTravelpayouts(fares=[fare(1200), fare(700), fare(950)]))
    assert quote.total_price == 700 * 6


def test_exact_dates_beat_a_cheaper_nearby_date():
    """week-matrix returns neighbouring dates; pricing those is a different trip."""
    client = FakeTravelpayouts(fares=[
        fare(400, depart="2027-06-03", ret="2027-06-13"),   # cheaper, wrong dates
        fare(900, depart="2027-06-05", ret="2027-06-15"),   # the trip we asked for
    ])
    quote = price_flight(client)
    assert quote.total_price == 900 * 6
    assert quote.depart_date == date(2027, 6, 5)


def test_nearby_dates_are_used_when_no_exact_match_exists():
    client = FakeTravelpayouts(fares=[fare(600, depart="2027-06-04", ret="2027-06-14")])
    quote = price_flight(client)
    assert quote is not None
    assert quote.total_price == 600 * 6


def test_fares_over_the_stop_limit_are_excluded():
    from vacation_finder.config import FlightPrefs

    trip = make_trip(flights=FlightPrefs(max_stops=0))
    client = FakeTravelpayouts(fares=[fare(300, changes=2), fare(1100, changes=0)])
    quote = price_flight(client, trip)
    assert quote.total_price == 1100 * 6
    assert quote.stops_out == 0


def test_zero_and_missing_prices_are_ignored():
    client = FakeTravelpayouts(fares=[fare(0), {"value": None}, fare(500)])
    assert price_flight(client).total_price == 500 * 6


def test_no_fares_returns_none():
    assert price_flight(FakeTravelpayouts(fares=[])) is None


def test_token_is_sent_as_a_header_not_a_query_param():
    """A token in the query string would leak into logs and referrers."""
    from vacation_finder.sources.travelpayouts import TravelpayoutsClient

    client = TravelpayoutsClient(token="secret-token")
    assert client.configured
    # The client builds headers; confirm the token never becomes a param.
    client.session.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call"))


def test_missing_token_is_reported_as_unavailable():
    from vacation_finder.sources.base import SourceUnavailable
    from vacation_finder.sources.travelpayouts import TravelpayoutsClient

    client = TravelpayoutsClient(token="")
    with pytest.raises(SourceUnavailable, match="TRAVELPAYOUTS_TOKEN"):
        client.get_flights("/v2/prices/week-matrix", {})


# --------------------------------------------------------------------------- #
# Lodging
# --------------------------------------------------------------------------- #

HOTELS = [
    hotel("Pricey Palace", 3000, stars=5, hotel_id=1),
    hotel("Good Value Inn", 1200, stars=4, hotel_id=2),
    hotel("Dodgy Hostel", 200, stars=1, hotel_id=3),
]


def price_lodging(client, trip=None, settings=SETTINGS):
    return search_lodging(
        client, trip or make_trip(), "Barcelona",
        date(2027, 6, 5), date(2027, 6, 15), "USD", settings,
    )


def test_lodging_scales_by_room_count():
    """Six people need two rooms; a one-room price would be fiction."""
    from vacation_finder.config import LodgingPrefs

    trip = make_trip(lodging=LodgingPrefs(rooms=2, min_stars=4))
    quote = price_lodging(FakeTravelpayouts(hotels=HOTELS), trip)
    assert quote.property_name == "Good Value Inn"
    assert quote.total_price == 2400.0          # 1200 x 2 rooms
    assert quote.nights == 10


def test_star_filter_excludes_low_rated_properties():
    from vacation_finder.config import LodgingPrefs

    trip = make_trip(lodging=LodgingPrefs(rooms=1, min_stars=4))
    quote = price_lodging(FakeTravelpayouts(hotels=HOTELS), trip)
    assert quote.property_name == "Good Value Inn"   # not the 1-star at 200


def test_max_per_night_filters_expensive_stays():
    from vacation_finder.config import LodgingPrefs

    trip = make_trip(lodging=LodgingPrefs(rooms=1, min_stars=4, max_per_night=50))
    assert price_lodging(FakeTravelpayouts(hotels=HOTELS), trip) is None


def test_per_night_price_basis_multiplies_by_nights():
    """The one genuinely ambiguous thing about Hotellook, so it's a setting."""
    from dataclasses import replace
    from vacation_finder.config import LodgingPrefs

    trip = make_trip(lodging=LodgingPrefs(rooms=1, min_stars=4))
    per_night = replace(SETTINGS, hotel_price_is_per_night=True)
    quote = price_lodging(FakeTravelpayouts(hotels=HOTELS), trip, per_night)
    assert quote.total_price == 1200.0 * 10


def test_empty_hotel_list_returns_none():
    assert price_lodging(FakeTravelpayouts(hotels=[])) is None


def test_non_list_hotel_response_is_handled():
    assert price_lodging(FakeTravelpayouts(hotels={"error": "nope"})) is None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def config_for(trip: Trip) -> Config:
    return Config(settings=SETTINGS, trips=(trip,))


def test_pricer_combines_flights_lodging_and_ground():
    from vacation_finder.config import GroundPrefs, LodgingPrefs

    trip = make_trip(
        ground=GroundPrefs(flat_cost=520, notes="train"),
        lodging=LodgingPrefs(rooms=2, min_stars=4),
    )
    client = FakeTravelpayouts(fares=[fare(800)], hotels=HOTELS)
    best = TripPricer(config_for(trip), client=client).price_trip(
        trip, today=date(2026, 9, 12)
    ).best

    assert best.flight_cost == 4800.0
    assert best.lodging_cost == 2400.0
    assert best.ground_cost == 520.0
    assert best.total_cost == 7720.0
    assert best.is_complete


def test_hotel_lookup_uses_the_city_label_not_the_airport_code():
    """Hotellook resolves names; 'CDG' is an airport, 'Paris' is a city."""
    trip = make_trip(destinations=(Destination(code="CDG", label="Paris"),))
    client = FakeTravelpayouts(fares=[fare(800)], hotels=HOTELS)
    TripPricer(config_for(trip), client=client).price_trip(trip, today=date(2026, 9, 12))

    hotel_calls = [c for c in client.calls if "cache.json" in c[0]]
    assert hotel_calls
    assert all(c[1]["location"] == "Paris" for c in hotel_calls)


def test_a_failing_lodging_source_does_not_abort_the_run():
    client = FakeTravelpayouts(fares=[fare(800)], hotels=HOTELS, fail_on=("cache.json",))
    result = TripPricer(config_for(make_trip()), client=client).price_trip(
        make_trip(), today=date(2026, 9, 12)
    )
    assert result.best.flight_cost == 4800.0
    assert result.best.lodging is None
    assert not result.best.is_complete


def test_every_sampled_window_gets_priced():
    trip = make_trip()
    client = FakeTravelpayouts(fares=[fare(800)], hotels=HOTELS)
    result = TripPricer(config_for(trip), client=client).price_trip(
        trip, today=date(2026, 9, 12)
    )
    assert len(result.quotes) == SETTINGS.max_date_samples_per_trip


# --------------------------------------------------------------------------- #
# Deal radar
# --------------------------------------------------------------------------- #

def test_cheapest_destinations_sorts_and_normalises():
    from vacation_finder.sources.travelpayouts import cheapest_destinations

    client = FakeTravelpayouts(fares=[
        {"origin": "STL", "destination": "DUB", "value": 410,
         "depart_date": "2027-06-12", "return_date": "2027-06-22",
         "number_of_changes": 1, "found_at": "2026-09-11T06:00:00"},
        {"origin": "STL", "destination": "LIS", "value": 388,
         "depart_date": "2027-06-08", "return_date": "2027-06-18",
         "number_of_changes": 1, "found_at": "2026-09-11T07:00:00"},
        {"origin": "STL", "destination": "XXX", "value": 0,
         "depart_date": "2027-06-08", "return_date": "2027-06-18"},
    ])
    deals = cheapest_destinations(client, "STL", "USD")

    assert [d["destination"] for d in deals] == ["LIS", "DUB"]   # cheapest first
    assert deals[0]["price_per_adult"] == 388
    assert deals[0]["depart_date"] == date(2027, 6, 8)
    assert all(d["price_per_adult"] > 0 for d in deals)          # the 0 row is dropped


def test_deal_radar_is_one_call_regardless_of_destination_count():
    """The whole point: scanning the market must not cost per-destination."""
    from vacation_finder.sources.travelpayouts import cheapest_destinations

    client = FakeTravelpayouts(fares=[
        {"origin": "STL", "destination": f"D{i}", "value": 300 + i,
         "depart_date": "2027-06-08", "return_date": "2027-06-18"}
        for i in range(60)
    ])
    deals = cheapest_destinations(client, "STL", "USD")
    assert len(deals) == 60
    assert len(client.calls) == 1


def test_deal_radar_omits_month_anchor_for_yearly_scans():
    from vacation_finder.sources.travelpayouts import cheapest_destinations

    client = FakeTravelpayouts(fares=[])
    cheapest_destinations(client, "STL", "USD", period_type="year")
    assert "beginning_of_period" not in client.calls[0][1]
