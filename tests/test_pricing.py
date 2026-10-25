"""Pricing tests against recorded-shape API payloads — no network."""

from datetime import date

import pytest

from vacation_finder.config import Config, DateWindow, Destination, Settings, Trip
from vacation_finder.models import Party
from vacation_finder.pricing import TripPricer
from vacation_finder.sources.base import SourceError
from vacation_finder.sources.travelpayouts import search_flights

SETTINGS = Settings(
    home_airports=("STL",),
    google_flights_enabled=False,   # keep the scraper out of unit tests
    lookahead_min_days=14,
    max_date_samples_per_trip=2,
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


def hotel(name: str, stay: float, stars: int = 4) -> dict:
    """One property as SerpApi's Google Hotels engine returns it."""
    return {
        "type": "hotel",
        "name": name,
        "property_token": f"tok-{name}",
        "total_rate": {"lowest": f"${stay:,.0f}", "extracted_lowest": stay},
        "extracted_hotel_class": stars,
        "overall_rating": 4.2,
    }


class FakeTravelpayouts:
    """Stands in for the client, returning canned payloads by path."""

    configured = True

    def __init__(self, fares=None, fail_on=()):
        self.fares = fares if fares is not None else []
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict]] = []

    def get_flights(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        if any(token in path for token in self.fail_on):
            raise SourceError(f"stubbed failure for {path}")
        return {"success": True, "data": self.fares}


class FakeHotels:
    """Stands in for the SerpApi client."""

    configured = True

    def __init__(self, properties=None, fail=False):
        self.properties = properties if properties is not None else []
        self.fail = fail
        self.calls: list[dict] = []

    def search(self, params: dict) -> dict:
        self.calls.append(params)
        if self.fail:
            raise SourceError("stubbed hotel failure")
        return {"properties": self.properties}


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
# Orchestration
# --------------------------------------------------------------------------- #

def config_for(trip: Trip) -> Config:
    return Config(settings=SETTINGS, trips=(trip,))


HOTELS = [
    hotel("Pricey Palace", 3000, stars=5),
    hotel("Good Value Inn", 1200, stars=4),
    hotel("Dodgy Hostel", 200, stars=1),
]


def pricer(trip, fares=(800,), hotels=None, **kwargs):
    client = FakeTravelpayouts(fares=[fare(v) for v in fares], **kwargs)
    return TripPricer(
        config_for(trip), client=client, hotels=hotels or FakeHotels(HOTELS)
    )


def test_pricer_combines_flights_lodging_and_ground():
    from vacation_finder.config import GroundPrefs, LodgingPrefs

    trip = make_trip(
        ground=GroundPrefs(flat_cost=520, notes="train"),
        lodging=LodgingPrefs(rooms=2, min_stars=4),
    )
    best = pricer(trip).price_trip(trip, today=date(2026, 9, 12)).best

    assert best.flight_cost == 4800.0
    assert best.lodging_cost == 2400.0
    assert best.ground_cost == 520.0
    assert best.total_cost == 7720.0
    assert best.is_complete


def test_hotel_lookup_searches_a_place_not_an_airport_code():
    """'CDG' is an airport; Google Hotels wants 'Paris'."""
    trip = make_trip(destinations=(Destination(code="CDG", label="Paris"),))
    hotels = FakeHotels(HOTELS)
    pricer(trip, hotels=hotels).price_trip(trip, today=date(2026, 9, 12))
    assert hotels.calls
    assert all(c["q"] == "Paris" for c in hotels.calls)


def test_hotel_query_overrides_the_label():
    trip = make_trip(
        destinations=(
            Destination(code="MUC", label="Munich / Bavarian Alps", hotel_query="Munich, Germany"),
        )
    )
    hotels = FakeHotels(HOTELS)
    pricer(trip, hotels=hotels).price_trip(trip, today=date(2026, 9, 12))
    assert all(c["q"] == "Munich, Germany" for c in hotels.calls)


def test_a_failing_lodging_source_does_not_abort_the_run():
    trip = make_trip()
    result = pricer(trip, hotels=FakeHotels(fail=True)).price_trip(
        trip, today=date(2026, 9, 12)
    )
    assert result.best.flight_cost == 4800.0
    assert result.best.lodging is None
    assert not result.best.is_complete


def test_missing_hotel_key_is_reported_once_per_trip_not_per_window():
    from vacation_finder.sources.google_hotels import SerpApiClient

    trip = make_trip()
    result = pricer(trip, hotels=SerpApiClient(api_key="")).price_trip(
        trip, today=date(2026, 9, 12)
    )
    hotel_errors = [e for e in result.errors if "SERPAPI_KEY" in e]
    assert len(hotel_errors) == 1


def test_travelpayouts_is_skipped_silently_without_a_token():
    """It's optional now; a missing token is not an error worth recording."""
    from vacation_finder.sources.travelpayouts import TravelpayoutsClient

    trip = make_trip()
    result = TripPricer(
        config_for(trip), client=TravelpayoutsClient(token=""), hotels=FakeHotels(HOTELS)
    ).price_trip(trip, today=date(2026, 9, 12))
    assert not any("travelpayouts" in e for e in result.errors)
    assert result.best.flight is None


def test_every_sampled_window_gets_priced():
    trip = make_trip()
    result = pricer(trip).price_trip(trip, today=date(2026, 9, 12))
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


def test_best_prefers_a_priced_airfare_over_a_date_with_nothing_found():
    """With no hotels yet, the date with no fare must not look cheapest."""
    from datetime import datetime, timezone

    from vacation_finder.models import FlightQuote, TripQuote
    from vacation_finder.pricing import RunResult

    def quote(depart, flight):
        d = date.fromisoformat(depart)
        return TripQuote(
            trip_id="t", destination="NCE", depart_date=d, return_date=d, nights=9,
            flight=FlightQuote(
                origin="STL", destination="NCE", depart_date=d, return_date=d,
                total_price=flight, currency="USD",
            ) if flight else None,
            ground_cost=260.0, flights_expected=True,
        )

    result = RunResult(
        trip_id="t", captured_at=datetime.now(timezone.utc),
        quotes=[quote("2027-06-05", 8154.0), quote("2027-06-21", None), quote("2027-07-08", None)],
    )
    assert result.best.flight_cost == 8154.0
