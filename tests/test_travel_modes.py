"""Driving trips and trips whose airfare isn't priced yet."""

from datetime import date
from pathlib import Path

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


# --------------------------------------------------------------------------- #
# Lodging coverage honesty
# --------------------------------------------------------------------------- #

def _cfg(tmp_path, coverage: str, extra: str = "") -> Path:
    path = tmp_path / "trips.yaml"
    path.write_text(
        "settings:\n  home_airports: [STL]\n"
        "trips:\n  - id: a\n    name: X\n"
        "    destinations: [ATH]\n"
        f"    lodging: {{coverage: {coverage}}}\n{extra}"
        "    dates: {mode: window, earliest: 2027-06-05, latest: 2027-07-10, nights: 7}\n"
    )
    return path


def test_coverage_none_turns_lodging_off():
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        trip = load_config(_cfg(pathlib.Path(d), "none")).trips[0]
    assert trip.lodging.coverage == "none"
    assert not trip.lodging.enabled


def test_coverage_thin_still_queries_lodging():
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        trip = load_config(_cfg(pathlib.Path(d), "thin")).trips[0]
    assert trip.lodging.enabled


def test_unknown_coverage_rejected():
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(ConfigError, match="coverage must be one of"):
            load_config(_cfg(pathlib.Path(d), "excellent"))


def test_thin_coverage_produces_a_caveat():
    import tempfile, pathlib
    from vacation_finder.report import TripReport

    with tempfile.TemporaryDirectory() as d:
        trip = load_config(_cfg(pathlib.Path(d), "thin")).trips[0]
    assert "floor" in TripReport(trip=trip).coverage_caveat


def test_good_coverage_produces_no_caveat():
    import tempfile, pathlib
    from vacation_finder.report import TripReport

    with tempfile.TemporaryDirectory() as d:
        trip = load_config(_cfg(pathlib.Path(d), "good")).trips[0]
    assert TripReport(trip=trip).coverage_caveat == ""


def test_untracked_lodging_says_so():
    import tempfile, pathlib
    from vacation_finder.report import TripReport

    with tempfile.TemporaryDirectory() as d:
        trip = load_config(_cfg(pathlib.Path(d), "none")).trips[0]
    assert "price it yourself" in TripReport(trip=trip).coverage_caveat


def test_lodging_only_total_is_labelled_as_excluding_airfare():
    """A total without airfare must never be presented as a trip cost."""
    import tempfile, pathlib
    from datetime import datetime, timezone
    from vacation_finder.email_report import trip_card
    from vacation_finder.report import TripReport
    from vacation_finder.storage import HistoryRow

    with tempfile.TemporaryDirectory() as d:
        trip = load_config(_cfg(pathlib.Path(d), "good", "    flights: {enabled: false}\n")).trips[0]

    row = HistoryRow(
        captured_at=datetime.now(timezone.utc), trip_id="a", destination="ATH",
        depart_date=date(2027, 6, 5), return_date=date(2027, 6, 12), nights=7,
        currency="USD", flight_total=0.0, flight_source="", flight_carriers="",
        lodging_name="Hotel", lodging_total=4000.0, lodging_per_night=571.0,
        lodging_rating=4.0, ground_total=300.0, total=4300.0, is_complete=True,
        travel_mode="air", flights_priced=False,
    )
    html = trip_card(TripReport(trip=trip, history=[row], latest=row))
    assert "excludes airfare" in html
    assert "not priced yet" in html


def test_drive_trip_total_is_a_real_total():
    import tempfile, pathlib
    from datetime import datetime, timezone
    from vacation_finder.email_report import trip_card
    from vacation_finder.report import TripReport
    from vacation_finder.storage import HistoryRow

    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "trips.yaml"
        path.write_text(
            "settings:\n  home_airports: [STL]\n"
            "trips:\n  - id: a\n    name: X\n    travel: drive\n"
            "    destinations: [BNA]\n"
            "    dates: {mode: window, earliest: 2027-03-12, latest: 2027-03-19, nights: 4}\n"
        )
        trip = load_config(path).trips[0]

    row = HistoryRow(
        captured_at=datetime.now(timezone.utc), trip_id="a", destination="BNA",
        depart_date=date(2027, 3, 12), return_date=date(2027, 3, 16), nights=4,
        currency="USD", flight_total=0.0, flight_source="", flight_carriers="",
        lodging_name="Inn", lodging_total=1600.0, lodging_per_night=400.0,
        lodging_rating=3.0, ground_total=157.0, total=1757.0, is_complete=True,
        travel_mode="drive", flights_priced=False,
    )
    html = trip_card(TripReport(trip=trip, history=[row], latest=row))
    assert "excludes airfare" not in html
    assert "not priced yet" not in html
