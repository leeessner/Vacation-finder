import textwrap

import pytest

from vacation_finder.config import ConfigError, load_config

BASE = """
settings:
  home_airports: [ORD]
trips:
  - id: {id}
    name: Test
    destinations: [CUN]
    dates:
      mode: window
      earliest: 2027-02-01
      latest: 2027-03-01
      nights: 7
"""


def write(tmp_path, body: str):
    path = tmp_path / "trips.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_minimal_config_loads(tmp_path):
    config = load_config(write(tmp_path, BASE.format(id="a")))
    trip = config.trips[0]
    assert trip.origins == ("ORD",)
    assert trip.destinations[0].code == "CUN"
    assert trip.party.adults == 2


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_duplicate_trip_ids_rejected(tmp_path):
    body = BASE.format(id="a") + (
        "  - id: a\n"
        "    name: Dup\n"
        "    destinations: [MBJ]\n"
        "    dates: {mode: window, earliest: 2027-02-01, latest: 2027-03-01, nights: 5}\n"
    )
    with pytest.raises(ConfigError, match="duplicate trip id"):
        load_config(write(tmp_path, body))


def test_trip_id_must_be_filename_safe(tmp_path):
    with pytest.raises(ConfigError, match="may only contain"):
        load_config(write(tmp_path, BASE.format(id="../escape")))


def test_children_must_be_ages_not_a_count(tmp_path):
    body = BASE.format(id="a").replace(
        "    dates:", "    party: {adults: 2, children: 3}\n    dates:"
    )
    with pytest.raises(ConfigError, match="list of ages"):
        load_config(write(tmp_path, body))


def test_inverted_window_rejected(tmp_path):
    body = BASE.format(id="a").replace("latest: 2027-03-01", "latest: 2027-01-01")
    with pytest.raises(ConfigError, match="on or after"):
        load_config(write(tmp_path, body))


def test_unknown_cabin_rejected(tmp_path):
    body = BASE.format(id="a") + "    flights:\n      cabin: luxury\n"
    with pytest.raises(ConfigError, match="cabin must be one of"):
        load_config(write(tmp_path, body))


def test_no_origins_anywhere_is_an_error(tmp_path):
    body = BASE.format(id="a").replace("  home_airports: [ORD]\n", "")
    with pytest.raises(ConfigError, match="no origins"):
        load_config(write(tmp_path, body))


def test_fixed_dates_require_ordered_pair(tmp_path):
    body = """
settings:
  home_airports: [ORD]
trips:
  - id: a
    name: Test
    destinations: [CUN]
    dates:
      mode: fixed
      depart: 2027-02-08
      return: 2027-02-01
"""
    with pytest.raises(ConfigError, match="must be after"):
        load_config(write(tmp_path, body))


def test_weekday_names_parse_to_indexes(tmp_path):
    body = BASE.format(id="a") + "      departure_weekdays: [Sat, sunday]\n"
    trip = load_config(write(tmp_path, body)).trips[0]
    assert trip.dates.departure_weekdays == (5, 6)


def test_active_trips_filters_inactive(tmp_path):
    body = BASE.format(id="a").replace("    name: Test", "    name: Test\n    active: false")
    config = load_config(write(tmp_path, body))
    assert config.trips and not config.active_trips


def test_shipped_config_is_valid():
    """The config committed to the repo must always load."""
    config = load_config("config/trips.yaml")
    assert config.trips
    assert all(t.destinations for t in config.trips)
