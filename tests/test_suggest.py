from datetime import date

from vacation_finder.config import Config, DateWindow, Destination, Settings, Trip
from vacation_finder.suggest import CatalogEntry, as_trip_yaml, suggest, suggest_for_trip

CATALOG = [
    CatalogEntry("PUJ", "Punta Cana", ("beach", "all-inclusive", "kid-friendly"), (1, 2, 3)),
    CatalogEntry("KEF", "Reykjavik", ("nature", "adventure"), (6, 7, 8)),
    CatalogEntry("AUA", "Aruba", ("beach", "resort"), (7, 8, 9)),
    CatalogEntry("CUN", "Cancun", ("beach", "all-inclusive"), (1, 2, 3)),
]


def beach_trip(**overrides) -> Trip:
    defaults = dict(
        id="beach",
        name="Beach week",
        destinations=(Destination(code="CUN"),),
        tags=("beach", "all-inclusive", "kid-friendly"),
        dates=DateWindow(mode="window", nights=7, earliest=date(2027, 2, 1), latest=date(2027, 2, 28)),
        origins=("ORD",),
    )
    defaults.update(overrides)
    return Trip(**defaults)


def test_suggestions_share_tags_with_the_trip():
    results = suggest_for_trip(beach_trip(), CATALOG, exclude={"CUN"})
    codes = [s.entry.code for s in results]
    assert "PUJ" in codes
    assert "KEF" not in codes


def test_already_tracked_destinations_are_excluded():
    results = suggest_for_trip(beach_trip(), CATALOG, exclude={"CUN"})
    assert all(s.entry.code != "CUN" for s in results)


def test_in_season_matches_outrank_off_season_ones():
    results = suggest_for_trip(beach_trip(), CATALOG, exclude={"CUN"})
    assert results[0].entry.code == "PUJ"   # Feb; Aruba's best months are Jul-Sep
    assert any("in season" in r for r in results[0].reasons)


def test_off_season_is_called_out_in_the_reasons():
    results = suggest_for_trip(beach_trip(), CATALOG, exclude={"CUN"})
    aruba = next(s for s in results if s.entry.code == "AUA")
    assert any("off-season" in r for r in aruba.reasons)


def test_untagged_trip_yields_no_suggestions():
    assert suggest_for_trip(beach_trip(tags=()), CATALOG, exclude=set()) == []


def test_suggest_deduplicates_across_trips():
    config = Config(
        settings=Settings(home_airports=("ORD",)),
        trips=(beach_trip(), beach_trip(id="beach2", name="Another beach week")),
    )
    results = suggest(config, CATALOG, limit=5)
    assert len({s.entry.code for s in results}) == len(results)


def test_generated_yaml_reparses_as_a_valid_trip(tmp_path):
    from vacation_finder.config import load_config

    trip = beach_trip()
    suggestion = suggest_for_trip(trip, CATALOG, exclude={"CUN"})[0]
    body = "settings:\n  home_airports: [ORD]\ntrips:\n" + as_trip_yaml(suggestion, trip)
    path = tmp_path / "trips.yaml"
    path.write_text(body)

    loaded = load_config(path).trips[0]
    assert loaded.destinations[0].code == "PUJ"
    assert loaded.party.children == trip.party.children
    assert loaded.dates.nights == trip.dates.nights
