"""Multi-stop trips (Barcelona then Madrid) and open-jaw flights."""

from datetime import date

import pytest

from vacation_finder import insights
from vacation_finder.config import Config, ConfigError, load_config
from vacation_finder.lodging import lodging_slots, slot_cost
from vacation_finder.pricing import TripPricer
from vacation_finder.sources import google_flights

from test_pricing import SETTINGS, FakeHotels, FakeTravelpayouts, hotel, make_trip

TODAY = date(2026, 10, 20)

YAML = """
settings:
  home_airports: [STL]
trips:
  - id: spain
    name: Spain
    stops:
      - code: BCN
        label: Barcelona
        nights: 5
        center: [41.39, 2.16]
        within_miles: 2
      - code: MAD
        label: Madrid
        nights: 5
        lodging: {kind: rental, bedrooms: 2, prefer: [Melia]}
    dates: {mode: window, earliest: 2027-06-05, latest: 2027-07-17}
    flights: {return_from: MAD}
    lodging: {rooms: 2, min_rating: 4.0}
  - id: alps
    name: Alps
    stops:
      - {code: MUC, label: Munich, nights: 5}
      - code: SZG
        label: Resort
        nights: 5
        lodging: {enabled: false}
        lodging_estimate: 6600
        lodging_note: "resort: estimate until a quote comes back"
    dates: {mode: window, earliest: 2027-06-05, latest: 2027-07-17}
"""


def load(tmp_path, text=YAML):
    path = tmp_path / "trips.yaml"
    path.write_text(text)
    return load_config(path)


def test_stops_set_the_nights_and_keep_their_own_lodging_rules(tmp_path):
    spain = load(tmp_path).trip("spain")
    assert spain.has_stops and spain.dates.nights == 10
    bcn, mad = spain.destinations
    assert spain.lodging_for(bcn).rooms == 2 and spain.lodging_for(bcn).kind == "hotel"
    madrid = spain.lodging_for(mad)
    assert madrid.kind == "rental" and madrid.bedrooms == 2
    assert madrid.min_rating == 4.0                  # inherited from the trip
    assert spain.return_airport == "MAD"


def test_stop_nights_must_agree_with_dates_nights(tmp_path):
    with pytest.raises(ConfigError, match="add up to 10"):
        load(tmp_path, YAML.replace(
            "dates: {mode: window, earliest: 2027-06-05, latest: 2027-07-17}\n    flights",
            "dates: {mode: window, earliest: 2027-06-05, latest: 2027-07-17, nights: 9}\n    flights",
        ))


def test_every_stop_needs_nights(tmp_path):
    with pytest.raises(ConfigError, match="needs 'nights'"):
        load(tmp_path, YAML.replace("      - {code: MUC, label: Munich, nights: 5}",
                                    "      - {code: MUC, label: Munich}"))


def test_legs_run_back_to_back(tmp_path):
    spain = load(tmp_path).trip("spain")
    legs = spain.legs(date(2027, 6, 5))
    assert [(s.code, a, b) for s, a, b in legs] == [
        ("BCN", date(2027, 6, 5), date(2027, 6, 10)),
        ("MAD", date(2027, 6, 10), date(2027, 6, 15)),
    ]


def test_each_priced_stop_is_its_own_hotel_slot(tmp_path):
    config = Config(settings=SETTINGS, trips=load(tmp_path).trips)
    slots = lodging_slots(config, TODAY)
    spain = [(d.code, a, b) for t, d, a, b in slots if t.id == "spain"]
    assert len(spain) == 2 * SETTINGS.max_date_samples_per_trip
    assert all((b - a).days == 5 for _, a, b in spain)
    assert [c for c, *_ in spain].count("MAD") == SETTINGS.max_date_samples_per_trip
    assert not [d for t, d, *_ in slots if t.id == "alps" and d.code == "SZG"]
    mad = config.trip("spain").destinations[1]
    assert slot_cost(config.trip("spain"), mad) == 2    # Madrid prefers a brand


def test_stays_add_up_to_one_lodging_line(tmp_path):
    config = Config(settings=SETTINGS, trips=load(tmp_path).trips)
    hotels = FakeHotels([hotel("Casa", 1000)])
    result = TripPricer(config, client=FakeTravelpayouts(), hotels=hotels).price_trip(
        config.trip("alps"), TODAY
    )
    quote = result.quotes[0]
    assert quote.destination == "MUC → SZG"
    assert quote.nights == 10
    assert quote.lodging.total_price == 1000 + 6600     # Munich searched + resort estimate
    assert "Resort (estimate)" in quote.lodging.property_name
    assert "resort: estimate until a quote comes back" in quote.notes
    assert len(hotels.calls) == SETTINGS.max_date_samples_per_trip   # only Munich searched


def test_a_stop_without_a_price_leaves_lodging_incomplete(tmp_path):
    config = Config(settings=SETTINGS, trips=load(tmp_path).trips)
    result = TripPricer(config, client=FakeTravelpayouts(), hotels=FakeHotels([])).price_trip(
        config.trip("alps"), TODAY
    )
    quote = result.quotes[0]
    assert quote.lodging is None
    assert any("no lodging price yet for Munich" in n for n in quote.notes)


def test_open_jaw_is_one_multi_city_search_home_from_the_last_city():
    query = google_flights.build_query(
        make_trip(), "STL", "BCN", date(2027, 6, 5), date(2027, 6, 15), "USD",
        return_from="MAD",
    )
    round_trip = google_flights.build_query(
        make_trip(), "STL", "BCN", date(2027, 6, 5), date(2027, 6, 15), "USD",
    )
    assert query.get_trip_type() == "multi-city"
    assert round_trip.get_trip_type() == "round-trip"


def test_weekly_check_asks_for_the_open_jaw(tmp_path):
    spain = load(tmp_path).trip("spain")
    config = Config(settings=SETTINGS, trips=(spain,))

    class Client:
        def search(self, params, engine="google_hotels"):
            self.params = params
            return {"best_flights": [{"price": 9000}]}

    client = Client()
    found = insights.fetch(client, spain, config, TODAY)
    assert client.params["type"] == 3
    assert '"departure_id": "MAD"' in client.params["multi_city_json"]
    assert found.lowest == 9000
