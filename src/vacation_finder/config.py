"""Loading and validation of config/trips.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .models import Party

WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

CABINS = {"economy", "premium-economy", "business", "first"}


class ConfigError(ValueError):
    """Raised with a human-readable message when trips.yaml is malformed."""


def _as_date(value: Any, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ConfigError(f"{where}: '{value}' is not a YYYY-MM-DD date") from exc
    raise ConfigError(f"{where}: expected a YYYY-MM-DD date, got {value!r}")


def _weekday(value: Any, where: str) -> int:
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    key = str(value).strip().lower()
    if key not in WEEKDAYS:
        raise ConfigError(f"{where}: '{value}' is not a weekday name")
    return WEEKDAYS[key]


@dataclass(frozen=True)
class Destination:
    code: str
    label: str = ""
    city_code: str = ""      # Amadeus city code for hotels; defaults to `code`
    hotel_ids: tuple[str, ...] = ()
    # What to type into Google Hotels, when the label isn't a searchable place
    # ("Munich / Bavarian Alps" reads well but searches badly).
    hotel_query: str = ""
    # Where you want to be, as (latitude, longitude), and how far from it a
    # place may be. The cheapest hotel "in Nashville" is 9 miles out; this is
    # what keeps the tracked price somewhere you'd actually stay.
    center: tuple[float, float] | None = None
    within_miles: float | None = None
    # Driving time from home. Within settings.max_drive_hours, a trip should
    # be a drive, not a flight; `validate` says so.
    drive_hours: float | None = None
    # Multi-stop trips (`stops:` in the config) only. How many nights this
    # stop gets, its own lodging rules (the trip's, with this stop's
    # overrides), and for a stay Google can't price (a resort that sells
    # direct), a fixed estimate used in its place.
    nights: int | None = None
    lodging: LodgingPrefs | None = None
    lodging_estimate: float | None = None
    lodging_note: str = ""

    @property
    def display(self) -> str:
        return self.label or self.code

    @property
    def lodging_query(self) -> str:
        return self.hotel_query or self.label or self.code

    @property
    def hotel_city(self) -> str:
        return self.city_code or self.code


@dataclass(frozen=True)
class DateWindow:
    mode: str                       # "window" or "fixed"
    nights: int
    earliest: date | None = None
    latest: date | None = None
    depart: date | None = None
    return_: date | None = None
    departure_weekdays: tuple[int, ...] = ()


@dataclass(frozen=True)
class FlightPrefs:
    # False when airfare shouldn't be priced at all — a driving trip, or a
    # departure so far out that airlines haven't loaded schedules yet.
    enabled: bool = True
    max_stops: int | None = 1
    cabin: str = "economy"
    checked_bags: int = 0
    carry_on_bags: int = 0
    exclude_basic_economy: bool = False
    # Fly home from a different airport than you arrived at (an open-jaw
    # ticket): arrive at the first stop's airport, leave from this one.
    return_from: str = ""
    # Overnight eastbound flights (St. Louis to Europe) land the day after
    # they leave. With this set, the flight leaves the day before the first
    # night's check-in, so the lodging dates are the nights you're there.
    overnight_outbound: bool = False


# How well the lodging price reflects what you'd actually book.
#   good — the number is the kind of place you'd book
#   thin — a usable floor, not the thing you'd book
#   none — not priced at all, rather than reporting a misleading number
COVERAGE_LEVELS = {"good", "thin", "none"}

# hotel  — N identical rooms (lodging.rooms) at one hotel
# rental — one vacation rental (cabin, apartment, house) for the whole party
LODGING_KINDS = {"hotel", "rental"}


@dataclass(frozen=True)
class LodgingPrefs:
    enabled: bool = True
    coverage: str = "good"
    kind: str = "hotel"
    rooms: int = 1
    bedrooms: int | None = None     # rentals only; defaults to `rooms`
    min_stars: float | None = None
    min_rating: float | None = None
    max_per_night: float | None = None
    board: str = ""
    property_names: tuple[str, ...] = ()
    # Hotel brands you'd pick over a cheaper alternative ("Embassy Suites").
    # Each costs one extra search per check; when one qualifies, it is the
    # tracked price and the cheapest alternative is noted beside it.
    prefer: tuple[str, ...] = ()
    # Words a property's name must not contain ("hostel"): Google rates
    # some hostels 3-star, so the star floor alone lets them through.
    avoid: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroundPrefs:
    rental_car: bool = False
    flat_cost: float = 0.0
    per_day_cost: float = 0.0
    # Driving trips: fuel is estimated from distance rather than looked up,
    # because no free API prices a road trip.
    drive_round_trip_miles: float = 0.0
    mpg: float = 25.0
    fuel_price_per_gallon: float = 3.25
    notes: str = ""

    @property
    def fuel_cost(self) -> float:
        if not self.drive_round_trip_miles or self.mpg <= 0:
            return 0.0
        return self.drive_round_trip_miles / self.mpg * self.fuel_price_per_gallon

    def cost_for(self, nights: int) -> float:
        return self.flat_cost + self.per_day_cost * nights + self.fuel_cost


@dataclass(frozen=True)
class AlertRules:
    total_below: float | None = None
    flight_below: float | None = None
    drop_pct_vs_median: float | None = 12.0
    percentile_below: float | None = 20.0
    all_time_low: bool = True
    cooldown_days: int = 3
    min_observations: int = 5


@dataclass(frozen=True)
class Trip:
    id: str
    name: str
    why: str = ""
    active: bool = True
    travel_mode: str = "air"        # "air" or "drive"
    origins: tuple[str, ...] = ()
    destinations: tuple[Destination, ...] = ()
    party: Party = field(default_factory=Party)
    dates: DateWindow = field(default_factory=lambda: DateWindow("window", 7))
    flights: FlightPrefs = field(default_factory=FlightPrefs)
    lodging: LodgingPrefs = field(default_factory=LodgingPrefs)
    ground: GroundPrefs = field(default_factory=GroundPrefs)
    target_total: float | None = None
    great_deal_total: float | None = None
    alerts: AlertRules = field(default_factory=AlertRules)
    tags: tuple[str, ...] = ()
    max_date_samples: int | None = None
    plan: str = ""                  # path to the trip's plan, e.g. plans/paris-alsace.md
    # "choice": `destinations` are alternatives, each priced for the whole
    # stay. "stops": `stops` are visited in order, each for its own nights,
    # and their lodging adds up to one stay.
    itinerary: str = "choice"

    @property
    def has_stops(self) -> bool:
        return self.itinerary == "stops"

    @property
    def return_airport(self) -> str:
        """Where the flight home leaves from (the arrival airport unless open-jaw)."""
        return self.flights.return_from or self.destinations[0].code

    def flight_dates(self, check_in: date, check_out: date) -> tuple[date, date]:
        """When the flights go, for a stay from check_in to check_out."""
        out = check_in - timedelta(days=1) if self.flights.overnight_outbound else check_in
        return out, check_out

    def lodging_for(self, destination: Destination) -> LodgingPrefs:
        return destination.lodging or self.lodging

    def legs(self, depart: date) -> list[tuple[Destination, date, date]]:
        """Each stop with its check-in and check-out, starting on `depart`."""
        out, day = [], depart
        for stop in self.destinations:
            end = day + timedelta(days=stop.nights or 0)
            out.append((stop, day, end))
            day = end
        return out


@dataclass(frozen=True)
class Settings:
    currency: str = "USD"
    home_airports: tuple[str, ...] = ()
    timezone: str = "America/New_York"
    max_date_samples_per_trip: int = 6
    lookahead_min_days: int = 14
    # SerpApi: 250 free searches a month, shared by hotel prices and weekly
    # Google Flights price insights. Each run spends at most
    # `searches_per_run` and keeps `hotel_search_reserve` back for Doctor and
    # manual runs; hotel prices not refreshed today are reused for up to
    # `hotel_price_max_age_days`.
    searches_per_run: int = 7
    hotel_search_reserve: int = 10
    hotel_price_max_age_days: int = 10
    flight_insight_days: int = 7
    # Within this many hours' drive, a trip should be driven, not flown.
    max_drive_hours: float = 6.0
    # When fares for long-haul trips are usually lowest, in months before
    # departure. Rough guidance from published booking studies, not a law.
    booking_window_months: tuple[int, int] = (5, 7)
    google_flights_enabled: bool = True
    google_flights_price_is_total: bool = True


@dataclass(frozen=True)
class Config:
    settings: Settings
    trips: tuple[Trip, ...]

    @property
    def active_trips(self) -> tuple[Trip, ...]:
        return tuple(t for t in self.trips if t.active)

    def trip(self, trip_id: str) -> Trip | None:
        return next((t for t in self.trips if t.id == trip_id), None)


def _parse_party(raw: Any, where: str) -> Party:
    if raw is None:
        return Party()
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 'party' must be a mapping")
    adults = int(raw.get("adults", 2))
    children_raw = raw.get("children", []) or []
    if isinstance(children_raw, int):
        raise ConfigError(
            f"{where}: 'party.children' must be a list of ages (e.g. [8, 5]), not a count"
        )
    children = tuple(int(age) for age in children_raw)
    if adults < 1:
        raise ConfigError(f"{where}: 'party.adults' must be at least 1")
    return Party(adults=adults, children=children)


def _parse_destinations(
    raw: Any, where: str, stops: bool = False, trip_lodging: dict | None = None
) -> tuple[Destination, ...]:
    if not raw:
        raise ConfigError(f"{where}: at least one destination is required")
    if isinstance(raw, str):
        raw = [raw]
    out: list[Destination] = []
    for item in raw:
        if isinstance(item, str):
            out.append(Destination(code=item.strip().upper()))
            continue
        if not isinstance(item, dict) or "code" not in item:
            raise ConfigError(f"{where}: each destination needs a 'code' (IATA airport code)")
        out.append(
            Destination(
                code=str(item["code"]).strip().upper(),
                label=str(item.get("label", "")),
                city_code=str(item.get("city_code", "")).strip().upper(),
                hotel_ids=tuple(str(h).strip().upper() for h in item.get("hotel_ids", []) or []),
                hotel_query=str(item.get("hotel_query", "")).strip(),
                center=_parse_center(item.get("center"), where),
                within_miles=_opt_float(item.get("within_miles")),
                drive_hours=_opt_float(item.get("drive_hours")),
                **(_stop_fields(item, where, trip_lodging or {}) if stops else {}),
            )
        )
    return tuple(out)


def _stop_fields(item: dict, where: str, trip_lodging: dict) -> dict[str, Any]:
    """The per-stop settings of a multi-stop trip."""
    code = str(item["code"]).strip().upper()
    nights = _opt_int(item.get("nights"))
    if not nights or nights < 1:
        raise ConfigError(f"{where}: stop {code} needs 'nights' (at least 1)")
    own = item.get("lodging") or {}
    if not isinstance(own, dict):
        raise ConfigError(f"{where}: stop {code}: 'lodging' must be a mapping")
    return {
        "nights": nights,
        "lodging": _parse_lodging({**trip_lodging, **own}, f"{where} stop {code}"),
        "lodging_estimate": _opt_float(item.get("lodging_estimate")),
        "lodging_note": str(item.get("lodging_note", "")),
    }


def _parse_dates(raw: Any, where: str) -> DateWindow:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 'dates' must be a mapping")
    mode = str(raw.get("mode", "window")).lower()
    if mode not in {"window", "fixed"}:
        raise ConfigError(f"{where}: dates.mode must be 'window' or 'fixed'")

    if mode == "fixed":
        depart = _as_date(raw.get("depart"), f"{where}.dates.depart")
        ret = _as_date(raw.get("return"), f"{where}.dates.return")
        if ret <= depart:
            raise ConfigError(f"{where}: dates.return must be after dates.depart")
        return DateWindow(mode="fixed", nights=(ret - depart).days, depart=depart, return_=ret)

    nights = int(raw.get("nights", 7))
    if nights < 1:
        raise ConfigError(f"{where}: dates.nights must be at least 1")
    earliest = _as_date(raw.get("earliest"), f"{where}.dates.earliest")
    latest = _as_date(raw.get("latest"), f"{where}.dates.latest")
    if latest < earliest:
        raise ConfigError(f"{where}: dates.latest must be on or after dates.earliest")
    weekdays = tuple(
        _weekday(d, f"{where}.dates.departure_weekdays")
        for d in raw.get("departure_weekdays", []) or []
    )
    return DateWindow(
        mode="window",
        nights=nights,
        earliest=earliest,
        latest=latest,
        departure_weekdays=weekdays,
    )


def _parse_lodging(lodging_raw: dict, where: str) -> LodgingPrefs:
    coverage = str(lodging_raw.get("coverage", "good")).lower()
    if coverage not in COVERAGE_LEVELS:
        raise ConfigError(
            f"{where}: lodging.coverage must be one of {sorted(COVERAGE_LEVELS)}"
        )
    kind = str(lodging_raw.get("kind", "hotel")).lower()
    if kind not in LODGING_KINDS:
        raise ConfigError(f"{where}: lodging.kind must be one of {sorted(LODGING_KINDS)}")
    return LodgingPrefs(
        # 'none' means we know the source can't see this lodging; querying it
        # would return an unrelated property and quietly mislead.
        enabled=bool(lodging_raw.get("enabled", True)) and coverage != "none",
        coverage=coverage,
        kind=kind,
        rooms=int(lodging_raw.get("rooms", 1)),
        bedrooms=_opt_int(lodging_raw.get("bedrooms")),
        min_stars=_opt_float(lodging_raw.get("min_stars")),
        min_rating=_opt_float(lodging_raw.get("min_rating")),
        max_per_night=_opt_float(lodging_raw.get("max_per_night")),
        board=str(lodging_raw.get("board", "")),
        property_names=tuple(str(p) for p in lodging_raw.get("properties", []) or []),
        prefer=tuple(str(p).strip() for p in lodging_raw.get("prefer", []) or [] if str(p).strip()),
        avoid=tuple(str(p).strip() for p in lodging_raw.get("avoid", ["hostel"]) or [] if str(p).strip()),
    )


def _parse_trip(raw: Any, index: int, settings: Settings) -> Trip:
    if not isinstance(raw, dict):
        raise ConfigError(f"trips[{index}]: each trip must be a mapping")
    trip_id = str(raw.get("id", "")).strip()
    if not trip_id:
        raise ConfigError(f"trips[{index}]: 'id' is required (used as the CSV filename)")
    if not all(ch.isalnum() or ch in "-_" for ch in trip_id):
        raise ConfigError(
            f"trips[{index}]: id '{trip_id}' may only contain letters, digits, '-' and '_'"
        )
    where = f"trip '{trip_id}'"

    origins = tuple(
        str(o).strip().upper() for o in (raw.get("origins") or settings.home_airports)
    )
    if not origins:
        raise ConfigError(
            f"{where}: no origins, and settings.home_airports is empty — set one of them"
        )

    travel_mode = str(raw.get("travel", "air")).lower()
    if travel_mode not in {"air", "drive"}:
        raise ConfigError(f"{where}: 'travel' must be 'air' or 'drive'")

    flights_raw = raw.get("flights") or {}
    cabin = str(flights_raw.get("cabin", "economy")).lower().replace("_", "-")
    if cabin not in CABINS:
        raise ConfigError(f"{where}: flights.cabin must be one of {sorted(CABINS)}")
    max_stops = flights_raw.get("max_stops", 1)
    flights = FlightPrefs(
        # A driving trip never prices airfare, whatever the flights block says.
        enabled=bool(flights_raw.get("enabled", True)) and travel_mode == "air",
        max_stops=None if max_stops is None else int(max_stops),
        cabin=cabin,
        checked_bags=int(flights_raw.get("checked_bags", 0)),
        carry_on_bags=int(flights_raw.get("carry_on_bags", 0)),
        exclude_basic_economy=bool(flights_raw.get("exclude_basic_economy", False)),
        return_from=str(flights_raw.get("return_from", "") or "").strip().upper(),
        overnight_outbound=bool(flights_raw.get("overnight_outbound", False)),
    )

    lodging_raw = raw.get("lodging") or {}
    lodging = _parse_lodging(lodging_raw, where)

    if "stops" in raw:
        if "destinations" in raw:
            raise ConfigError(f"{where}: use either 'destinations' or 'stops', not both")
        itinerary = "stops"
        destinations = _parse_destinations(
            raw.get("stops"), where, stops=True, trip_lodging=lodging_raw
        )
        total = sum(d.nights or 0 for d in destinations)
        dates_raw = dict(raw.get("dates") or {})
        if "nights" in dates_raw and int(dates_raw["nights"]) != total:
            raise ConfigError(
                f"{where}: dates.nights is {dates_raw['nights']} but the stops add up "
                f"to {total} — drop dates.nights, it is worked out from the stops"
            )
        dates_raw["nights"] = total
    else:
        itinerary = "choice"
        destinations = _parse_destinations(raw.get("destinations"), where)
        dates_raw = raw.get("dates")

    ground_raw = raw.get("ground") or {}
    ground = GroundPrefs(
        rental_car=bool(ground_raw.get("rental_car", False)),
        flat_cost=float(ground_raw.get("flat_cost", 0) or 0),
        per_day_cost=float(ground_raw.get("per_day_cost", 0) or 0),
        drive_round_trip_miles=float(ground_raw.get("drive_round_trip_miles", 0) or 0),
        mpg=float(ground_raw.get("mpg", 25) or 25),
        fuel_price_per_gallon=float(ground_raw.get("fuel_price_per_gallon", 3.25) or 3.25),
        notes=str(ground_raw.get("notes", "")),
    )

    budget_raw = raw.get("budget") or {}
    alerts_raw = raw.get("alerts") or {}
    alerts = AlertRules(
        total_below=_opt_float(alerts_raw.get("total_below")),
        flight_below=_opt_float(alerts_raw.get("flight_below")),
        drop_pct_vs_median=_opt_float(alerts_raw.get("drop_pct_vs_median", 12.0)),
        percentile_below=_opt_float(alerts_raw.get("percentile_below", 20.0)),
        all_time_low=bool(alerts_raw.get("all_time_low", True)),
        cooldown_days=int(alerts_raw.get("cooldown_days", 3)),
        min_observations=int(alerts_raw.get("min_observations", 5)),
    )

    return Trip(
        id=trip_id,
        name=str(raw.get("name", trip_id)),
        why=str(raw.get("why", "")),
        active=bool(raw.get("active", True)),
        travel_mode=travel_mode,
        origins=origins,
        destinations=destinations,
        party=_parse_party(raw.get("party"), where),
        dates=_parse_dates(dates_raw, where),
        flights=flights,
        lodging=lodging,
        ground=ground,
        target_total=_opt_float(budget_raw.get("target_total")),
        great_deal_total=_opt_float(budget_raw.get("great_deal_total")),
        alerts=alerts,
        tags=tuple(str(t).strip().lower() for t in raw.get("tags", []) or []),
        max_date_samples=_opt_int(raw.get("max_date_samples")),
        plan=str(raw.get("plan", "") or ""),
        itinerary=itinerary,
    )


def _parse_center(raw: Any, where: str) -> tuple[float, float] | None:
    if raw is None:
        return None
    try:
        lat, lng = (float(v) for v in raw)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: 'center' must be [latitude, longitude]") from None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ConfigError(f"{where}: 'center' {raw} is not a valid [latitude, longitude]")
    return (lat, lng)


def _parse_window(raw: Any) -> tuple[int, int]:
    if raw is None:
        return (5, 7)
    try:
        low, high = (int(v) for v in raw)
    except (TypeError, ValueError):
        raise ConfigError("settings.booking_window_months must be [low, high]") from None
    if not 0 < low <= high:
        raise ConfigError("settings.booking_window_months must be [low, high] with low <= high")
    return (low, high)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _parse_settings(raw: Any) -> Settings:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError("'settings' must be a mapping")
    if "amadeus_env" in raw:
        raise ConfigError(
            "settings.amadeus_env is obsolete — the Amadeus Self-Service free tier "
            "was decommissioned on 2026-07-17. Remove this line; the tracker now "
            "uses Travelpayouts (see SETUP.md)."
        )
    return Settings(
        currency=str(raw.get("currency", "USD")).upper(),
        home_airports=tuple(str(a).strip().upper() for a in raw.get("home_airports", []) or []),
        timezone=str(raw.get("timezone", "America/New_York")),
        max_date_samples_per_trip=int(raw.get("max_date_samples_per_trip", 6)),
        lookahead_min_days=int(raw.get("lookahead_min_days", 14)),
        searches_per_run=int(
            raw.get("searches_per_run", raw.get("hotel_searches_per_run", 7))
        ),
        hotel_search_reserve=int(raw.get("hotel_search_reserve", 10)),
        hotel_price_max_age_days=int(raw.get("hotel_price_max_age_days", 10)),
        flight_insight_days=int(raw.get("flight_insight_days", 7)),
        max_drive_hours=float(raw.get("max_drive_hours", 6.0)),
        booking_window_months=_parse_window(raw.get("booking_window_months")),
        google_flights_enabled=bool(raw.get("google_flights_enabled", True)),
        google_flights_price_is_total=bool(raw.get("google_flights_price_is_total", True)),
    )


def load_config(path: str | Path = "config/trips.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping with 'settings' and 'trips'")

    settings = _parse_settings(raw.get("settings"))
    trips_raw = raw.get("trips") or []
    if not isinstance(trips_raw, list):
        raise ConfigError("'trips' must be a list")

    trips = tuple(_parse_trip(t, i, settings) for i, t in enumerate(trips_raw))
    seen: set[str] = set()
    for trip in trips:
        if trip.id in seen:
            raise ConfigError(f"duplicate trip id '{trip.id}'")
        seen.add(trip.id)
    return Config(settings=settings, trips=trips)
