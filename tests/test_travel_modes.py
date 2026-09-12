"""Driving trips and trips whose airfare isn't priced yet."""

from datetime import date

import pytest

from vacation_finder.config import ConfigError, Destination, GroundPrefs, load_config
from vacation_finder.models import LodgingQuote, TripQuote
from vacation_finder.pricing import TripPricer
from vacation_finder.config import Config, DateWindow, Settings, Trip
from vacation_finder.models import Party

from test_pricing import FakeAmadeus, HOTELS, OFFERS, flight_offer

SETTINGS = Settings(
    home_airports=("STL",), google_flights_enabled=False, max_date_samples_per_trip=2
)


def lodging() -> LodgingQuote:
    return LodgingQuote(
        destination="BNA", check_in=date(2027, 3, 6), check_out=date(2027, 3, 10),
        property_name="Inn", total_price=900.0, currency="USD", nights=4,
    )


def test_drive_trip_is_complete_without_airfare():
    quote = TripQuote(
        trip_id="t", destination="BNA", depart_date=date(2027, 3, 6),
        return_date=date(2027, 3, 10), nights=4, lodging=lodging(),
        travel_mode="drive", flights_expected=False, ground_cost=120.0,
    )
    assert quote.is_complete
    assert quote.total_cost == 1020.0


def test_air_trip_without_airfare_is_incomplete():
    quote = TripQuote(
        trip_id="t", destination="ATH", depart_date=date(2027, 3, 6),
        return_date=date(2027, 3, 10), nights=4, lodging=lodging(),
        travel_mode="air", flights_expected=True,
    )
    assert not quote.is_complete


def test_lodging_only_air_trip_is_complete_when_fares_are_deliberately_skipped():
    quote = TripQuote(
        trip_id="t", destination="ATH", depart_date=date(2027, 7, 6),
        return_date=date(2027, 7, 10), nights=4, lodging=lodging(),
        travel_mode="air", flights_expected=False,
    )
    assert quote.is_complete


def test_fuel_cost_from_distance():
    ground = GroundPrefs(drive_round_trip_miles=600, mpg=20, fuel_price_per_gallon=3.00)
    assert ground.fuel_cost == pytest.approx(90.0)
    assert ground.cost_for(4) == pytest.approx(90.0)


def test_fuel_cost_is_zero_without_a_distance():
    assert GroundPrefs(mpg=20).fuel_cost == 0.0


def test_drive_trip_spends_no_flight_api_calls():
    from vacation_finder.config import FlightPrefs

    trip = Trip(
        id="drive", name="Drive", travel_mode="drive",
        flights=FlightPrefs(enabled=False),
        origins=("STL",), destinations=(Destination(code="BNA"),),
        party=Party(adults=2, children=(8, 7, 5, 3)),
        dates=DateWindow(mode="window", nights=4,
                         earliest=date(2027, 3, 1), latest=date(2027, 3, 20)),
    )
    client = FakeAmadeus(flights=[flight_offer("9999.00", 1, 1)], hotels=HOTELS, offers=OFFERS)
    result = TripPricer(Config(settings=SETTINGS, trips=(trip,)), client=client).price_trip(
        trip, today=date(2026, 9, 12)
    )
    assert not any("flight-offers" in call[0] for call in client.calls)
    assert result.best.flight is None
    assert result.best.is_complete


def test_travel_mode_drive_forces_flights_off(tmp_path):
    path = tmp_path / "trips.yaml"
    path.write_text(
        "settings:\n  home_airports: [STL]\n"
        "trips:\n  - id: a\n    name: Drive\n    travel: drive\n"
        "    destinations: [BNA]\n    flights: {enabled: true}\n"
        "    dates: {mode: window, earliest: 2027-03-01, latest: 2027-03-20, nights: 4}\n"
    )
    trip = load_config(path).trips[0]
    assert trip.travel_mode == "drive"
    assert not trip.flights.enabled


def test_unknown_travel_mode_rejected(tmp_path):
    path = tmp_path / "trips.yaml"
    path.write_text(
        "settings:\n  home_airports: [STL]\n"
        "trips:\n  - id: a\n    name: X\n    travel: teleport\n"
        "    destinations: [BNA]\n"
        "    dates: {mode: window, earliest: 2027-03-01, latest: 2027-03-20, nights: 4}\n"
    )
    with pytest.raises(ConfigError, match="'air' or 'drive'"):
        load_config(path)


def test_history_row_labels_airfare_applicability():
    from vacation_finder.storage import HistoryRow
    from datetime import datetime, timezone

    def row(**kw):
        base = dict(
            captured_at=datetime.now(timezone.utc), trip_id="t", destination="BNA",
            depart_date=date(2027, 3, 6), return_date=date(2027, 3, 10), nights=4,
            currency="USD", flight_total=0.0, flight_source="", flight_carriers="",
            lodging_name="Inn", lodging_total=900.0, lodging_per_night=225.0,
            lodging_rating=None, ground_total=120.0, total=1020.0, is_complete=True,
        )
        base.update(kw)
        return HistoryRow(**base)

    assert not row(travel_mode="drive").airfare_applies
    assert not row(travel_mode="air", flights_priced=False).airfare_applies
    assert row(travel_mode="air", flights_priced=True).airfare_applies
