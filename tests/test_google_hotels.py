"""Google Hotels (via SerpApi) — params, filtering, and keeping the key secret."""

from datetime import date

import pytest
import requests

from vacation_finder.config import LodgingPrefs
from vacation_finder.models import Party
from vacation_finder.sources import base
from vacation_finder.sources.base import SourceError, SourceUnavailable
from vacation_finder.sources.google_hotels import (
    SerpApiClient,
    room_occupancy,
    search_lodging,
    search_params,
)

from test_pricing import SETTINGS, FakeHotels, make_trip

CHECK_IN, CHECK_OUT = date(2027, 6, 5), date(2027, 6, 15)   # 10 nights


def prop(name, stay=None, nightly=None, stars=4, rating=4.3, kind="hotel"):
    out = {"type": kind, "name": name, "property_token": f"tok-{name}",
           "overall_rating": rating}
    if stars is not None:
        out["extracted_hotel_class"] = stars
    if stay is not None:
        out["total_rate"] = {"extracted_lowest": stay}
    if nightly is not None:
        out["rate_per_night"] = {"extracted_lowest": nightly}
    return out


def price(properties, **lodging):
    trip = make_trip(lodging=LodgingPrefs(**lodging))
    return search_lodging(
        FakeHotels(properties), trip, "Barcelona", CHECK_IN, CHECK_OUT, "USD", SETTINGS
    )


# --------------------------------------------------------------------------- #
# Who is in the priced room
# --------------------------------------------------------------------------- #

def test_six_people_in_two_rooms_is_three_per_room_oldest_kids_priced():
    assert room_occupancy(Party(adults=2, children=(5, 8, 3, 7)), 2) == (1, (8, 7))


def test_one_room_holds_everyone():
    assert room_occupancy(Party(adults=2, children=(8, 3)), 1) == (2, (8, 3))


def test_hotel_search_params():
    trip = make_trip(lodging=LodgingPrefs(rooms=2, min_stars=3))
    params = search_params(trip, "Barcelona", CHECK_IN, CHECK_OUT, "USD")
    assert params["adults"] == 1
    assert params["children"] == 2
    assert params["children_ages"] == "8,7"
    assert params["hotel_class"] == "3,4,5"
    assert params["sort_by"] == 3
    assert params["check_in_date"] == "2027-06-05"
    assert "vacation_rentals" not in params


def test_rental_search_prices_the_whole_party_in_one_place():
    trip = make_trip(lodging=LodgingPrefs(kind="rental", rooms=2, bedrooms=3, min_stars=4))
    params = search_params(trip, "Gatlinburg, TN", CHECK_IN, CHECK_OUT, "USD")
    assert params["adults"] == 2
    assert params["children"] == 4
    assert params["vacation_rentals"] == "true"
    assert params["bedrooms"] == 3
    assert "hotel_class" not in params        # stars mean nothing for a cabin


def test_infant_ages_are_sent_as_one():
    """Google takes children aged 1-17; a baby is priced as a one-year-old."""
    trip = make_trip(party=Party(adults=2, children=(0,)), lodging=LodgingPrefs(rooms=1))
    assert search_params(trip, "x", CHECK_IN, CHECK_OUT, "USD")["children_ages"] == "1"


def test_min_rating_maps_to_googles_codes():
    trip = make_trip(lodging=LodgingPrefs(min_rating=4.0))
    assert search_params(trip, "x", CHECK_IN, CHECK_OUT, "USD")["rating"] == 8


# --------------------------------------------------------------------------- #
# Choosing the price
# --------------------------------------------------------------------------- #

def test_hotel_stay_is_multiplied_by_rooms():
    quote = price([prop("Inn", stay=1500)], rooms=2)
    assert quote.total_price == 3000.0
    assert quote.nights == 10
    assert quote.source == "google_hotels"
    assert quote.property_id == "tok-Inn"


def test_cheapest_qualifying_property_wins():
    quote = price(
        [prop("Palace", stay=4000, stars=5), prop("Inn", stay=1500), prop("Hostel", stay=300, stars=1)],
        rooms=1, min_stars=3,
    )
    assert quote.property_name == "Inn"


def test_unrated_property_is_excluded_when_stars_are_required():
    assert price([prop("Mystery", stay=500, stars=None)], min_stars=3) is None


def test_nightly_rate_is_used_when_no_stay_total_is_given():
    assert price([prop("Inn", nightly=150)], rooms=1).total_price == 1500.0


def test_max_per_night_applies_across_all_rooms():
    # 2 rooms x 1500 = 3000 for 10 nights = 300 a night
    assert price([prop("Inn", stay=1500)], rooms=2, max_per_night=299) is None
    assert price([prop("Inn", stay=1500)], rooms=2, max_per_night=300) is not None


def test_rental_is_one_unit_and_ignores_hotels():
    quote = price(
        [prop("Chain", stay=900), prop("Cabin", stay=2200, stars=None, kind="vacation rental")],
        kind="rental", rooms=2, min_stars=3,
    )
    assert quote.property_name == "Cabin"
    assert quote.total_price == 2200.0         # not doubled


def test_hotel_search_ignores_rentals_mixed_into_results():
    quote = price(
        [prop("Cabin", stay=100, kind="vacation rental"), prop("Inn", stay=1500)], rooms=1
    )
    assert quote.property_name == "Inn"


def test_no_properties_returns_none():
    assert price([]) is None


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #

class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response, self.exc = response, exc
        self.params = []

    def request(self, method, url, timeout=None, **kwargs):
        self.params.append(kwargs.get("params"))
        if self.exc:
            raise self.exc
        return self.response


def client_with(session) -> SerpApiClient:
    client = SerpApiClient(api_key="sk-secret-123")
    client.session = session
    client.limiter = None
    return client


def test_missing_key_is_unavailable():
    with pytest.raises(SourceUnavailable, match="SERPAPI_KEY"):
        SerpApiClient(api_key="").search({})


def test_search_sends_the_engine_and_key():
    session = FakeSession(FakeResponse(payload={"properties": []}))
    client_with(session).search({"q": "Paris"})
    assert session.params[0]["engine"] == "google_hotels"
    assert session.params[0]["api_key"] == "sk-secret-123"


def test_the_key_never_appears_in_an_error(monkeypatch):
    """Errors land in logs and in the history CSV, which is committed."""
    monkeypatch.setattr(base.time, "sleep", lambda _s: None)
    leak = requests.ConnectionError("failed: /search.json?q=x&api_key=sk-secret-123")
    with pytest.raises(SourceError) as info:
        client_with(FakeSession(exc=leak)).search({"q": "x"})
    assert "sk-secret-123" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_rejected_key_is_unavailable():
    with pytest.raises(SourceUnavailable):
        client_with(FakeSession(FakeResponse(401, {"error": "Invalid API key"}))).search({})


def test_no_results_error_is_a_source_error():
    payload = {"error": "Google Hotels hasn't returned any results for this query."}
    with pytest.raises(SourceError, match="any results"):
        client_with(FakeSession(FakeResponse(200, payload))).search({})


def test_searches_left_reads_the_account():
    session = FakeSession(FakeResponse(payload={"total_searches_left": 187}))
    assert client_with(session).searches_left() == 187


def test_searches_left_is_unknown_without_a_key_or_on_failure():
    assert SerpApiClient(api_key="").searches_left() is None
    session = FakeSession(FakeResponse(500, {"error": "down"}))
    session.response.status_code = 404     # not retried, just an error
    assert client_with(session).searches_left() is None

