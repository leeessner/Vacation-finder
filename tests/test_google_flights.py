"""The Google Flights scraper's fallback for big parties on long-haul routes."""

from dataclasses import dataclass
from datetime import date

from vacation_finder.config import FlightPrefs
from vacation_finder.models import Party
from vacation_finder.sources import google_flights

from test_pricing import SETTINGS, make_trip

DEPART, RETURN = date(2027, 6, 5), date(2027, 6, 15)


@dataclass
class Result:
    price: int
    airlines: tuple = ("AA",)


def fake_google(monkeypatch, by_party):
    """`by_party` maps passenger count — or (count, max stops) — to results;
    anything else gets the TypeError fast-flights raises on an empty page."""
    seen = []

    def get_flights(query):
        count = len(query.passengers)
        stops = query.flight_data[0].max_stops if query.flight_data[0].HasField("max_stops") else None
        seen.append((count, query.checked_bags))
        outcome = by_party.get((count, stops), by_party.get(count, TypeError("'NoneType' object is not subscriptable")))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(google_flights, "get_flights", get_flights)
    return seen


def search(trip):
    return google_flights.search_flights(trip, "STL", "BCN", DEPART, RETURN, "USD", SETTINGS)


def test_whole_party_price_is_used_when_google_lists_it(monkeypatch):
    seen = fake_google(monkeypatch, {6: [Result(8100), Result(7900)]})
    quote = search(make_trip())
    assert quote.total_price == 7900
    assert quote.source == "google_flights"
    assert [c for c, _ in seen] == [6]


def test_empty_six_seat_page_falls_back_to_two_adults_scaled(monkeypatch):
    seen = fake_google(monkeypatch, {2: [Result(2745), Result(2900)]})
    quote = search(make_trip(flights=FlightPrefs(checked_bags=3)))
    assert quote.total_price == 2745 / 2 * 6
    assert quote.source == "google_flights_scaled"
    assert seen == [(6, 3), (2, 0)]     # the stand-in drops the bag filter


def test_nothing_anywhere_returns_none(monkeypatch):
    fake_google(monkeypatch, {})
    assert search(make_trip()) is None


def test_a_couple_is_scaled_only_from_one_adult(monkeypatch):
    seen = fake_google(monkeypatch, {})
    assert search(make_trip(party=Party(adults=2), flights=FlightPrefs(max_stops=None))) is None
    assert [c for c, _ in seen] == [2, 1]


def test_one_adult_rung_when_two_adults_find_nothing(monkeypatch):
    fake_google(monkeypatch, {1: [Result(1857)]})
    quote = search(make_trip())
    assert quote.total_price == 1857 * 6
    assert quote.source == "google_flights_scaled"


def test_extra_stop_rung_prices_the_whole_party(monkeypatch):
    # Munich: nothing within one stop for anyone; the family appears at two.
    fake_google(monkeypatch, {(6, 2): [Result(7896)]})
    quote = search(make_trip(flights=FlightPrefs(max_stops=1)))
    assert quote.total_price == 7896
    assert quote.source == "google_flights_extra_stop"


def test_ladder_order():
    steps = google_flights.fallback_ladder(make_trip(flights=FlightPrefs(max_stops=1)))
    assert [(s.adults, s.extra_stops) for s in steps] == [
        (None, 0), (2, 0), (1, 0), (None, 1), (2, 1)
    ]


def test_scaled_fare_is_noted_on_the_quote(monkeypatch):
    from vacation_finder.config import Config
    from vacation_finder.pricing import TripPricer

    from test_pricing import FakeHotels, FakeTravelpayouts, HOTELS

    fake_google(monkeypatch, {2: [Result(2745)]})
    trip = make_trip()
    settings = SETTINGS.__class__(**{**SETTINGS.__dict__, "google_flights_enabled": True})
    result = TripPricer(
        Config(settings=settings, trips=(trip,)),
        client=FakeTravelpayouts(), hotels=FakeHotels(HOTELS),
    ).price_trip(trip, today=date(2026, 9, 12))
    quote = result.quotes[0]
    assert quote.flight.source == "google_flights_scaled"
    assert any("smaller party's fare scaled" in n for n in quote.notes)
