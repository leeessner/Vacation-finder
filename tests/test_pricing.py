"""Pricing tests against recorded-shape Amadeus payloads — no network."""

from datetime import date

import pytest

from vacation_finder.config import Config, Destination, DateWindow, Settings, Trip
from vacation_finder.models import Party
from vacation_finder.pricing import TripPricer
from vacation_finder.sources.amadeus import search_flights, search_lodging
from vacation_finder.sources.base import SourceError

SETTINGS = Settings(
    home_airports=("ORD",),
    google_flights_enabled=False,   # keep the scraper out of unit tests
    max_hotels_per_query=5,
    lookahead_min_days=14,
    max_date_samples_per_trip=2,
)


def flight_offer(total: str, out_segments: int, back_segments: int, carrier: str = "AA") -> dict:
    return {
        "price": {"currency": "USD", "total": total, "grandTotal": total},
        "itineraries": [
            {"segments": [{"carrierCode": carrier}] * out_segments},
            {"segments": [{"carrierCode": carrier}] * back_segments},
        ],
        "validatingAirlineCodes": [carrier],
    }


class FakeAmadeus:
    """Stands in for AmadeusClient.get, returning canned payloads by path."""

    def __init__(self, flights=None, hotels=None, offers=None, fail_on=()):
        self.flights = flights if flights is not None else []
        self.hotels = hotels if hotels is not None else []
        self.offers = offers if offers is not None else []
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        if any(token in path for token in self.fail_on):
            raise SourceError(f"stubbed failure for {path}")
        if "flight-offers" in path:
            return {"data": self.flights}
        if "hotels/by-city" in path:
            return {"data": self.hotels}
        if "hotel-offers" in path:
            return {"data": self.offers}
        return {"data": []}


def make_trip(**overrides) -> Trip:
    defaults = dict(
        id="t",
        name="Test trip",
        origins=("ORD",),
        destinations=(Destination(code="CUN"),),
        party=Party(adults=2, children=(8, 1)),
        dates=DateWindow(
            mode="window", nights=7, earliest=date(2027, 2, 1), latest=date(2027, 2, 20)
        ),
    )
    defaults.update(overrides)
    return Trip(**defaults)


# --------------------------------------------------------------------------- #
# Flights
# --------------------------------------------------------------------------- #

def test_picks_the_cheapest_offer():
    client = FakeAmadeus(flights=[
        flight_offer("1800.00", 1, 1),
        flight_offer("1200.00", 1, 1, "DL"),
        flight_offer("1500.00", 1, 1),
    ])
    quote = search_flights(
        client, make_trip(), "ORD", "CUN", date(2027, 2, 1), date(2027, 2, 8), "USD"
    )
    assert quote.total_price == 1200.0
    assert quote.carriers == ("DL",)


def test_offers_over_the_stop_limit_are_excluded():
    from vacation_finder.config import FlightPrefs

    trip = make_trip(flights=FlightPrefs(max_stops=0))
    client = FakeAmadeus(flights=[
        flight_offer("900.00", 3, 3),     # two stops each way
        flight_offer("1400.00", 1, 1),    # nonstop
    ])
    quote = search_flights(
        client, trip, "ORD", "CUN", date(2027, 2, 1), date(2027, 2, 8), "USD"
    )
    assert quote.total_price == 1400.0
    assert quote.stops_out == 0


def test_one_way_offers_are_ignored():
    client = FakeAmadeus(flights=[{
        "price": {"currency": "USD", "grandTotal": "600.00"},
        "itineraries": [{"segments": [{}]}],
    }])
    assert search_flights(
        client, make_trip(), "ORD", "CUN", date(2027, 2, 1), date(2027, 2, 8), "USD"
    ) is None


def test_infants_and_children_are_split_for_the_api():
    client = FakeAmadeus(flights=[flight_offer("1000.00", 1, 1)])
    search_flights(client, make_trip(), "ORD", "CUN", date(2027, 2, 1), date(2027, 2, 8), "USD")
    params = client.calls[0][1]
    assert params["adults"] == 2        # the 8- and 1-year-old are not adults
    assert params["children"] == 1
    assert params["infants"] == 1


def test_no_offers_returns_none():
    assert search_flights(
        FakeAmadeus(flights=[]), make_trip(), "ORD", "CUN",
        date(2027, 2, 1), date(2027, 2, 8), "USD",
    ) is None


# --------------------------------------------------------------------------- #
# Lodging
# --------------------------------------------------------------------------- #

HOTELS = [{"hotelId": "AAA"}, {"hotelId": "BBB"}]
OFFERS = [
    {
        "hotel": {"hotelId": "AAA", "name": "Pricey Resort", "rating": "5"},
        "offers": [{"price": {"currency": "USD", "total": "3500.00"},
                    "boardType": "ALL_INCLUSIVE", "policies": {}}],
    },
    {
        "hotel": {"hotelId": "BBB", "name": "Good Value Inn", "rating": "4"},
        "offers": [{"price": {"currency": "USD", "total": "1400.00"},
                    "boardType": "ROOM_ONLY", "policies": {}}],
    },
]


def test_lodging_picks_the_cheapest_and_computes_per_night():
    quote = search_lodging(
        FakeAmadeus(hotels=HOTELS, offers=OFFERS), make_trip(), "CUN",
        date(2027, 2, 1), date(2027, 2, 8), "USD", SETTINGS,
    )
    assert quote.property_name == "Good Value Inn"
    assert quote.total_price == 1400.0
    assert quote.nights == 7
    assert quote.per_night == pytest.approx(200.0)


def test_max_per_night_filters_out_expensive_properties():
    from vacation_finder.config import LodgingPrefs

    trip = make_trip(lodging=LodgingPrefs(max_per_night=100))
    assert search_lodging(
        FakeAmadeus(hotels=HOTELS, offers=OFFERS), trip, "CUN",
        date(2027, 2, 1), date(2027, 2, 8), "USD", SETTINGS,
    ) is None


def test_no_hotels_in_city_returns_none():
    assert search_lodging(
        FakeAmadeus(hotels=[], offers=[]), make_trip(), "CUN",
        date(2027, 2, 1), date(2027, 2, 8), "USD", SETTINGS,
    ) is None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def config_for(trip: Trip) -> Config:
    return Config(settings=SETTINGS, trips=(trip,))


def test_pricer_combines_flights_lodging_and_ground():
    from vacation_finder.config import GroundPrefs

    trip = make_trip(ground=GroundPrefs(flat_cost=140, notes="transfer"))
    pricer = TripPricer(config_for(trip), client=FakeAmadeus(
        flights=[flight_offer("1200.00", 1, 1)], hotels=HOTELS, offers=OFFERS
    ))
    result = pricer.price_trip(trip, today=date(2026, 9, 1))
    best = result.best
    assert best.flight_cost == 1200.0
    assert best.lodging_cost == 1400.0
    assert best.ground_cost == 140.0
    assert best.total_cost == 2740.0
    assert best.is_complete


def test_quote_is_incomplete_when_lodging_is_missing():
    pricer = TripPricer(config_for(make_trip()), client=FakeAmadeus(
        flights=[flight_offer("1200.00", 1, 1)], hotels=[], offers=[]
    ))
    result = pricer.price_trip(make_trip(), today=date(2026, 9, 1))
    assert result.best is not None
    assert not result.best.is_complete


def test_a_failing_source_does_not_abort_the_run():
    pricer = TripPricer(config_for(make_trip()), client=FakeAmadeus(
        flights=[flight_offer("1200.00", 1, 1)],
        hotels=HOTELS,
        offers=OFFERS,
        fail_on=("hotel-offers",),
    ))
    result = pricer.price_trip(make_trip(), today=date(2026, 9, 1))
    assert result.best.flight_cost == 1200.0
    assert result.best.lodging is None


def test_city_hotel_list_is_fetched_once_per_run_not_per_date():
    """The hotel list is stable within a run; refetching it per date wastes quota."""
    client = FakeAmadeus(flights=[flight_offer("1200.00", 1, 1)], hotels=HOTELS, offers=OFFERS)
    trip = make_trip()
    TripPricer(config_for(trip), client=client).price_trip(trip, today=date(2026, 9, 1))
    city_lookups = [c for c in client.calls if "hotels/by-city" in c[0]]
    date_windows = {c[1]["checkInDate"] for c in client.calls if "hotel-offers" in c[0]}
    assert len(city_lookups) == 1
    assert len(date_windows) > 1


def test_every_sampled_window_gets_priced():
    client = FakeAmadeus(flights=[flight_offer("1200.00", 1, 1)], hotels=HOTELS, offers=OFFERS)
    trip = make_trip()
    result = TripPricer(config_for(trip), client=client).price_trip(trip, today=date(2026, 9, 1))
    assert len(result.quotes) == SETTINGS.max_date_samples_per_trip
