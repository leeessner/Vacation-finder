"""Loading and validation of config/trips.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
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

    @property
    def display(self) -> str:
        return self.label or self.code

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
    max_stops: int | None = 1
    cabin: str = "economy"
    checked_bags: int = 0
    carry_on_bags: int = 0
    exclude_basic_economy: bool = False


@dataclass(frozen=True)
class LodgingPrefs:
    enabled: bool = True
    rooms: int = 1
    min_stars: float | None = None
    min_rating: float | None = None
    max_per_night: float | None = None
    board: str = ""
    property_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroundPrefs:
    rental_car: bool = False
    flat_cost: float = 0.0
    per_day_cost: float = 0.0
    notes: str = ""

    def cost_for(self, nights: int) -> float:
        return self.flat_cost + self.per_day_cost * nights


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


@dataclass(frozen=True)
class Settings:
    currency: str = "USD"
    home_airports: tuple[str, ...] = ()
    timezone: str = "America/New_York"
    max_date_samples_per_trip: int = 6
    max_hotels_per_query: int = 20
    lookahead_min_days: int = 14
    amadeus_env: str = "test"
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


def _parse_destinations(raw: Any, where: str) -> tuple[Destination, ...]:
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
            )
        )
    return tuple(out)


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

    flights_raw = raw.get("flights") or {}
    cabin = str(flights_raw.get("cabin", "economy")).lower().replace("_", "-")
    if cabin not in CABINS:
        raise ConfigError(f"{where}: flights.cabin must be one of {sorted(CABINS)}")
    max_stops = flights_raw.get("max_stops", 1)
    flights = FlightPrefs(
        max_stops=None if max_stops is None else int(max_stops),
        cabin=cabin,
        checked_bags=int(flights_raw.get("checked_bags", 0)),
        carry_on_bags=int(flights_raw.get("carry_on_bags", 0)),
        exclude_basic_economy=bool(flights_raw.get("exclude_basic_economy", False)),
    )

    lodging_raw = raw.get("lodging") or {}
    lodging = LodgingPrefs(
        enabled=bool(lodging_raw.get("enabled", True)),
        rooms=int(lodging_raw.get("rooms", 1)),
        min_stars=_opt_float(lodging_raw.get("min_stars")),
        min_rating=_opt_float(lodging_raw.get("min_rating")),
        max_per_night=_opt_float(lodging_raw.get("max_per_night")),
        board=str(lodging_raw.get("board", "")),
        property_names=tuple(str(p) for p in lodging_raw.get("properties", []) or []),
    )

    ground_raw = raw.get("ground") or {}
    ground = GroundPrefs(
        rental_car=bool(ground_raw.get("rental_car", False)),
        flat_cost=float(ground_raw.get("flat_cost", 0) or 0),
        per_day_cost=float(ground_raw.get("per_day_cost", 0) or 0),
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
        origins=origins,
        destinations=_parse_destinations(raw.get("destinations"), where),
        party=_parse_party(raw.get("party"), where),
        dates=_parse_dates(raw.get("dates"), where),
        flights=flights,
        lodging=lodging,
        ground=ground,
        target_total=_opt_float(budget_raw.get("target_total")),
        great_deal_total=_opt_float(budget_raw.get("great_deal_total")),
        alerts=alerts,
        tags=tuple(str(t).strip().lower() for t in raw.get("tags", []) or []),
        max_date_samples=_opt_int(raw.get("max_date_samples")),
    )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _parse_settings(raw: Any) -> Settings:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError("'settings' must be a mapping")
    amadeus_env = str(raw.get("amadeus_env", "test")).lower()
    if amadeus_env not in {"test", "production"}:
        raise ConfigError("settings.amadeus_env must be 'test' or 'production'")
    return Settings(
        currency=str(raw.get("currency", "USD")).upper(),
        home_airports=tuple(str(a).strip().upper() for a in raw.get("home_airports", []) or []),
        timezone=str(raw.get("timezone", "America/New_York")),
        max_date_samples_per_trip=int(raw.get("max_date_samples_per_trip", 6)),
        max_hotels_per_query=int(raw.get("max_hotels_per_query", 20)),
        lookahead_min_days=int(raw.get("lookahead_min_days", 14)),
        amadeus_env=amadeus_env,
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
