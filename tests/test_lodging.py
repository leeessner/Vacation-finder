"""Spreading 250 hotel searches a month across every trip and date window."""

from datetime import date, datetime, timezone

from vacation_finder.config import Config, DateWindow, Destination, LodgingPrefs
from vacation_finder.lodging import LodgingBook, lodging_slots, run_allowance, slot_key
from vacation_finder.models import LodgingQuote
from vacation_finder.pricing import TripPricer
from vacation_finder.stats import assess
from vacation_finder.storage import HistoryRow

from test_pricing import HOTELS, SETTINGS, FakeHotels, FakeTravelpayouts, fare, make_trip

TODAY = date(2026, 10, 20)


def trips(n=3):
    return tuple(
        make_trip(
            id=f"t{i}",
            destinations=(Destination(code="BCN", label="Barcelona"),),
            lodging=LodgingPrefs(rooms=2, min_stars=4),
        )
        for i in range(n)
    )


def config(n=3):
    return Config(settings=SETTINGS, trips=trips(n))


def keys(cfg):
    return [
        slot_key(t.id, d.lodging_query, a, b) for t, d, a, b in lodging_slots(cfg, TODAY)
    ]


def lodging(price=2000.0):
    return LodgingQuote(
        destination="Barcelona", check_in=date(2027, 6, 5), check_out=date(2027, 6, 15),
        property_name="Inn", total_price=price, currency="USD", nights=10,
        source="google_hotels",
    )


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

def test_slots_are_every_active_trip_times_every_window():
    cfg = config(3)
    assert len(lodging_slots(cfg, TODAY)) == 3 * SETTINGS.max_date_samples_per_trip


def test_allowance_caps_the_searches():
    book = LodgingBook.plan(config(3), TODAY, allowance=2)
    assert len(book.refresh) == 2


def test_never_searched_slots_go_first_then_the_stalest():
    cfg = config(2)
    all_keys = keys(cfg)
    entries = {k: {"checked_on": "2026-10-19", "quote": None} for k in all_keys}
    entries[all_keys[1]]["checked_on"] = "2026-10-10"     # stalest
    del entries[all_keys[3]]                               # never searched
    book = LodgingBook.plan(cfg, TODAY, allowance=2, entries=entries)
    assert book.refresh == {all_keys[3], all_keys[1]}


def test_slots_no_trip_prices_any_more_are_dropped():
    entries = {"gone|Paris|2027-01-01|2027-01-05": {"checked_on": "2026-10-01", "quote": None}}
    book = LodgingBook.plan(config(1), TODAY, allowance=0, entries=entries)
    assert book.entries == {}


def test_run_allowance():
    assert run_allowance(None, per_run=7, reserve=10) == 7       # unknown: cap applies
    assert run_allowance(200, per_run=7, reserve=10) == 7
    assert run_allowance(14, per_run=7, reserve=10) == 4         # keep the reserve
    assert run_allowance(3, per_run=7, reserve=10) == 0


# --------------------------------------------------------------------------- #
# The cache
# --------------------------------------------------------------------------- #

def test_recorded_quote_round_trips_through_the_cache():
    book = LodgingBook(today=TODAY, max_age_days=10)
    book.record("k", lodging(2345.0))
    quote, age = book.cached("k")
    assert quote.total_price == 2345.0
    assert quote.check_in == date(2027, 6, 5)
    assert age == 0


def test_old_prices_expire():
    book = LodgingBook(
        today=TODAY, max_age_days=10,
        entries={"k": {"checked_on": "2026-10-05", "quote": lodging().to_dict()}},
    )
    assert book.cached("k") == (None, 15)


def test_a_search_that_found_nothing_is_remembered_but_not_priced():
    book = LodgingBook(today=TODAY, max_age_days=10)
    book.record("k", None)
    assert book.entries["k"]["checked_on"] == TODAY.isoformat()
    assert book.cached("k") == (None, None)


# --------------------------------------------------------------------------- #
# The pricer with a budget
# --------------------------------------------------------------------------- #

def test_pricer_searches_only_planned_slots_and_reuses_the_rest():
    cfg = config(1)
    trip = cfg.trips[0]
    first, second = keys(cfg)
    book = LodgingBook(
        today=TODAY, max_age_days=10, refresh={first},
        entries={second: {"checked_on": "2026-10-17", "quote": lodging(1800.0).to_dict()}},
    )
    hotels = FakeHotels(HOTELS)
    result = TripPricer(
        cfg, client=FakeTravelpayouts(fares=[fare(800)]), hotels=hotels, book=book
    ).price_trip(trip, today=TODAY)

    assert len(hotels.calls) == 1                      # only the planned slot
    by_date = {q.depart_date: q for q in result.quotes}
    searched, reused = by_date[date.fromisoformat(first.split("|")[2])], by_date[
        date.fromisoformat(second.split("|")[2])
    ]
    assert searched.lodging.total_price == 2400.0      # Good Value Inn x 2 rooms
    assert reused.lodging.total_price == 1800.0
    assert "hotel price checked 3 day(s) ago" in reused.notes
    assert book.entries[first]["checked_on"] == TODAY.isoformat()


def test_unsearched_slot_with_no_history_reports_why():
    cfg = config(1)
    book = LodgingBook(today=TODAY, max_age_days=10, unavailable_reason="SERPAPI_KEY is not set")
    result = TripPricer(
        cfg, client=FakeTravelpayouts(fares=[fare(800)]), hotels=FakeHotels(HOTELS), book=book
    ).price_trip(cfg.trips[0], today=TODAY)
    assert result.errors == ["hotel prices unavailable: SERPAPI_KEY is not set"]
    assert all(q.lodging is None for q in result.quotes)


# --------------------------------------------------------------------------- #
# History: rows without a hotel are not deals
# --------------------------------------------------------------------------- #

def row(day: int, total: float, complete: bool) -> HistoryRow:
    return HistoryRow(
        captured_at=datetime(2026, 10, day, tzinfo=timezone.utc), trip_id="t",
        destination="BNA", depart_date=date(2027, 3, 12), return_date=date(2027, 3, 16),
        nights=4, currency="USD", flight_total=0.0, flight_source="", flight_carriers="",
        lodging_name="Inn" if complete else "", lodging_total=total - 157 if complete else 0.0,
        lodging_per_night=0.0, lodging_rating=None, ground_total=157.0, total=total,
        is_complete=complete, travel_mode="drive", flights_priced=False,
    )


def test_incomplete_rows_do_not_count_toward_the_total_assessment():
    history = [row(d, 157.0, False) for d in range(6, 19)] + [
        row(d, 1500.0 + d, True) for d in range(19, 25)
    ]
    assessment = assess(history, field="total")
    assert assessment.observations == 6
    assert assessment.all_time_low > 1000
