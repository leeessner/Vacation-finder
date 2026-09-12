"""Append-only CSV price history plus per-run JSON snapshots.

One CSV row per trip per run holds the best option found that day — that is the
time series the charts and "is this a good price" logic read. The JSON snapshot
beside it keeps every option we priced, so a surprising number can be explained
after the fact.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .models import TripQuote
from .pricing import RunResult

HISTORY_DIR = Path("data/history")
SNAPSHOT_DIR = Path("data/snapshots")
STATE_DIR = Path("data/state")

FIELDS = [
    "captured_at",
    "trip_id",
    "destination",
    "depart_date",
    "return_date",
    "nights",
    "currency",
    "travel_mode",
    "flights_priced",
    "flight_total",
    "flight_source",
    "flight_carriers",
    "flight_stops_out",
    "flight_stops_back",
    "lodging_name",
    "lodging_total",
    "lodging_per_night",
    "lodging_rating",
    "lodging_source",
    "ground_total",
    "total",
    "is_complete",
    "options_priced",
    "notes",
]


@dataclass(frozen=True)
class HistoryRow:
    """One observation of a trip's best price."""

    captured_at: datetime
    trip_id: str
    destination: str
    depart_date: date
    return_date: date
    nights: int
    currency: str
    flight_total: float
    flight_source: str
    flight_carriers: str
    lodging_name: str
    lodging_total: float
    lodging_per_night: float
    lodging_rating: float | None
    ground_total: float
    total: float
    is_complete: bool
    notes: str = ""
    travel_mode: str = "air"
    flights_priced: bool = True

    @property
    def airfare_applies(self) -> bool:
        """False for a driving trip, or one whose flights we aren't pricing yet."""
        return self.travel_mode == "air" and self.flights_priced

    @property
    def captured_on(self) -> date:
        return self.captured_at.date()


def history_path(trip_id: str, base: Path = HISTORY_DIR) -> Path:
    return base / f"{trip_id}.csv"


def append_run(
    result: RunResult, base: Path = HISTORY_DIR, snapshots: Path = SNAPSHOT_DIR
) -> HistoryRow | None:
    """Record a run: one history row for the best option, a snapshot for all of them."""
    write_snapshot(result, snapshots)
    best = result.best
    if best is None:
        return None

    row = _row_from_quote(result, best)
    path = history_path(result.trip_id, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
    return _parse_row(row)


def _row_from_quote(result: RunResult, quote: TripQuote) -> dict[str, str]:
    flight, lodging = quote.flight, quote.lodging
    notes = list(quote.notes) + result.errors
    return {
        "captured_at": result.captured_at.replace(microsecond=0).isoformat(),
        "trip_id": quote.trip_id,
        "destination": quote.destination,
        "depart_date": quote.depart_date.isoformat(),
        "return_date": quote.return_date.isoformat(),
        "nights": str(quote.nights),
        "currency": quote.currency,
        "travel_mode": quote.travel_mode,
        "flights_priced": "true" if quote.flights_expected else "false",
        "flight_total": f"{quote.flight_cost:.2f}",
        "flight_source": flight.source if flight else "",
        "flight_carriers": "/".join(flight.carriers) if flight else "",
        "flight_stops_out": "" if not flight or flight.stops_out is None else str(flight.stops_out),
        "flight_stops_back": (
            "" if not flight or flight.stops_back is None else str(flight.stops_back)
        ),
        "lodging_name": lodging.property_name if lodging else "",
        "lodging_total": f"{quote.lodging_cost:.2f}",
        "lodging_per_night": f"{lodging.per_night:.2f}" if lodging else "0.00",
        "lodging_rating": "" if not lodging or lodging.rating is None else f"{lodging.rating:g}",
        "lodging_source": lodging.source if lodging else "",
        "ground_total": f"{quote.ground_cost:.2f}",
        "total": f"{quote.total_cost:.2f}",
        "is_complete": "true" if quote.is_complete else "false",
        "options_priced": str(len(result.quotes)),
        "notes": " | ".join(n for n in notes if n),
    }


def write_snapshot(result: RunResult, base: Path = SNAPSHOT_DIR) -> Path:
    directory = base / result.trip_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.captured_at.date().isoformat()}.json"
    path.write_text(
        json.dumps(
            {
                "trip_id": result.trip_id,
                "captured_at": result.captured_at.isoformat(),
                "queries_made": result.queries_made,
                "errors": result.errors,
                "quotes": [q.to_dict() for q in result.quotes],
            },
            indent=2,
        )
    )
    return path


def load_history(trip_id: str, base: Path = HISTORY_DIR) -> list[HistoryRow]:
    path = history_path(trip_id, base)
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        rows = [_parse_row(r) for r in csv.DictReader(handle)]
    return sorted((r for r in rows if r is not None), key=lambda r: r.captured_at)


def _parse_row(raw: dict[str, str]) -> HistoryRow | None:
    try:
        return HistoryRow(
            captured_at=datetime.fromisoformat(raw["captured_at"]),
            trip_id=raw["trip_id"],
            destination=raw.get("destination", ""),
            depart_date=date.fromisoformat(raw["depart_date"]),
            return_date=date.fromisoformat(raw["return_date"]),
            nights=int(raw.get("nights") or 0),
            currency=raw.get("currency", "USD"),
            travel_mode=raw.get("travel_mode", "air"),
            flights_priced=str(raw.get("flights_priced", "true")).lower() != "false",
            flight_total=_float(raw.get("flight_total")),
            flight_source=raw.get("flight_source", ""),
            flight_carriers=raw.get("flight_carriers", ""),
            lodging_name=raw.get("lodging_name", ""),
            lodging_total=_float(raw.get("lodging_total")),
            lodging_per_night=_float(raw.get("lodging_per_night")),
            lodging_rating=_opt_float(raw.get("lodging_rating")),
            ground_total=_float(raw.get("ground_total")),
            total=_float(raw.get("total")),
            is_complete=str(raw.get("is_complete", "")).lower() == "true",
            notes=raw.get("notes", ""),
        )
    except (KeyError, ValueError):
        return None


def _float(value: str | None) -> float:
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0


def _opt_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def load_state(name: str, base: Path = STATE_DIR) -> dict:
    path = base / f"{name}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_state(name: str, payload: dict, base: Path = STATE_DIR) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return path


def all_tracked_trip_ids(base: Path = HISTORY_DIR) -> list[str]:
    if not base.exists():
        return []
    return sorted(p.stem for p in base.glob("*.csv"))


def iter_history(trip_ids: Iterable[str], base: Path = HISTORY_DIR):
    for trip_id in trip_ids:
        yield trip_id, load_history(trip_id, base)
