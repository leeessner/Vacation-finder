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


def test_first_look_prices_one_window_of_each_listed_trip(tmp_path):
    from vacation_finder.lodging import LodgingBook, first_look_slots

    config = Config(settings=SETTINGS, trips=load(tmp_path).trips)
    look = first_look_slots(config, TODAY, ["spain"])
    assert len(look) == 2                       # Barcelona + Madrid, first window only
    assert sum(look.values()) == 3              # Madrid's preferred brand costs one more

    # Everything else is stalest-first; the first look jumps the queue.
    old = {k: {"checked_on": "2020-01-01", "quote": None} for k in look}
    book = LodgingBook.plan(config, TODAY, allowance=3, entries=old, priority=list(look))
    assert book.refresh == set(look)
    book = LodgingBook.plan(config, TODAY, allowance=3, entries=old)
    assert book.refresh != set(look)            # without it, never-searched slots go first


def test_first_look_rejects_an_unknown_trip(tmp_path, monkeypatch):
    from vacation_finder import cli

    path = tmp_path / "trips.yaml"
    path.write_text(YAML)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    assert cli.main(["track", "--config", str(path), "--first-look", "nowhere"]) == 2


def test_a_flight_check_for_other_dates_is_redone(tmp_path):
    from datetime import timedelta

    from vacation_finder.dates import candidate_windows
    from vacation_finder.insights import FlightInsight, due

    spain = load(tmp_path).trip("spain")
    config = Config(settings=SETTINGS, trips=(spain,))
    depart, ret = candidate_windows(spain, TODAY, SETTINGS)[0]

    def check(ret_on):
        return {"spain": FlightInsight(trip_id="spain", checked_on=TODAY, depart=depart,
                                       return_=ret_on, lowest=9000.0, level="",
                                       typical_low=None, typical_high=None,
                                       short_layovers=True)}

    assert due(config, check(ret), TODAY) == []                       # same dates, fresh
    assert due(config, check(ret - timedelta(days=2)), TODAY) == [spain]   # trip got longer


def test_first_look_skips_a_slot_already_priced_today(tmp_path):
    from vacation_finder.lodging import LodgingBook, first_look_slots

    config = Config(settings=SETTINGS, trips=load(tmp_path).trips)
    look = list(first_look_slots(config, TODAY, ["spain"]))
    entries = {look[0]: {"checked_on": TODAY.isoformat(), "quote": {"property_name": "x"}}}
    book = LodgingBook.plan(config, TODAY, allowance=1, entries=entries, priority=look)
    assert look[0] not in book.refresh


def test_weekly_hotels_price_the_first_window_and_wait_a_week(tmp_path):
    from dataclasses import replace
    from datetime import timedelta

    from vacation_finder.lodging import LodgingBook, slot_key

    trips = load(tmp_path).trips
    settings = replace(SETTINGS, hotel_windows_per_trip=1, hotel_recheck_days=7)
    config = Config(settings=settings, trips=trips)
    slots = lodging_slots(config, TODAY)
    assert len([s for s in slots if s[0].id == "spain"]) == 2     # Barcelona + Madrid, once

    keys = [slot_key(t.id, d.lodging_query, a, b) for t, d, a, b in slots]
    six_days = {k: {"checked_on": (TODAY - timedelta(days=6)).isoformat(), "quote": None} for k in keys}
    assert LodgingBook.plan(config, TODAY, allowance=20, entries=six_days).refresh == set()
    week = {k: {"checked_on": (TODAY - timedelta(days=7)).isoformat(), "quote": None} for k in keys}
    assert LodgingBook.plan(config, TODAY, allowance=20, entries=week).refresh == set(keys)
