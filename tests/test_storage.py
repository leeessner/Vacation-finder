from datetime import date, datetime, timezone

from vacation_finder.models import FlightQuote, LodgingQuote, TripQuote
from vacation_finder.pricing import RunResult
from vacation_finder.storage import append_run, load_history, load_state, save_state


def quote(total_flight=1200.0, total_lodging=1400.0, ground=140.0) -> TripQuote:
    depart, ret = date(2027, 2, 1), date(2027, 2, 8)
    return TripQuote(
        trip_id="t",
        destination="CUN",
        depart_date=depart,
        return_date=ret,
        nights=7,
        flight=FlightQuote(
            origin="ORD", destination="CUN", depart_date=depart, return_date=ret,
            total_price=total_flight, currency="USD", carriers=("AA",),
            stops_out=0, stops_back=1, source="amadeus",
        ),
        lodging=LodgingQuote(
            destination="CUN", check_in=depart, check_out=ret,
            property_name="Test Resort", total_price=total_lodging, currency="USD",
            nights=7, rating=4.0, source="amadeus",
        ),
        ground_cost=ground,
    )


def run(*quotes) -> RunResult:
    return RunResult(
        trip_id="t",
        captured_at=datetime.now(timezone.utc),
        quotes=list(quotes),
    )


def test_round_trip_through_csv(tmp_path):
    row = append_run(run(quote()), base=tmp_path, snapshots=tmp_path / "snap")
    assert row.total == 2740.0

    loaded = load_history("t", base=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].flight_total == 1200.0
    assert loaded[0].lodging_name == "Test Resort"
    assert loaded[0].is_complete


def test_appending_preserves_prior_rows(tmp_path):
    append_run(run(quote(total_flight=1200)), base=tmp_path, snapshots=tmp_path / "s")
    append_run(run(quote(total_flight=900)), base=tmp_path, snapshots=tmp_path / "s")
    history = load_history("t", base=tmp_path)
    assert [r.flight_total for r in history] == [1200.0, 900.0]


def test_only_the_cheapest_option_is_recorded(tmp_path):
    row = append_run(
        run(quote(total_flight=2000), quote(total_flight=800), quote(total_flight=1500)),
        base=tmp_path,
        snapshots=tmp_path / "s",
    )
    assert row.flight_total == 800.0


def test_complete_quotes_beat_cheaper_incomplete_ones(tmp_path):
    """A quote missing lodging looks cheap but isn't comparable."""
    partial = quote()
    partial.lodging = None
    row = append_run(run(partial, quote()), base=tmp_path, snapshots=tmp_path / "s")
    assert row.is_complete
    assert row.total == 2740.0


def test_snapshot_captures_every_option_priced(tmp_path):
    import json

    append_run(
        run(quote(total_flight=2000), quote(total_flight=800)),
        base=tmp_path,
        snapshots=tmp_path / "snap",
    )
    files = list((tmp_path / "snap" / "t").glob("*.json"))
    assert len(files) == 1
    assert len(json.loads(files[0].read_text())["quotes"]) == 2


def test_run_with_no_quotes_writes_no_row(tmp_path):
    assert append_run(run(), base=tmp_path, snapshots=tmp_path / "s") is None
    assert load_history("t", base=tmp_path) == []


def test_history_for_unknown_trip_is_empty(tmp_path):
    assert load_history("never-tracked", base=tmp_path) == []


def test_malformed_rows_are_skipped_not_fatal(tmp_path):
    append_run(run(quote()), base=tmp_path, snapshots=tmp_path / "s")
    path = tmp_path / "t.csv"
    path.write_text(path.read_text() + "garbage,row,that,does,not,parse\n")
    assert len(load_history("t", base=tmp_path)) == 1


def test_state_round_trip(tmp_path):
    save_state("alerts", {"t:all_time_low": {"last_price": 3400}}, base=tmp_path)
    assert load_state("alerts", base=tmp_path)["t:all_time_low"]["last_price"] == 3400


def test_missing_state_is_an_empty_dict(tmp_path):
    assert load_state("nothing-here", base=tmp_path) == {}
