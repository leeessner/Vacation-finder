"""One spreadsheet of every trip's price over time: data/prices-over-time.csv.

The per-trip CSVs in data/history/ are built for the tracker — 24 columns,
one file per trip. This is built for a person: one row per day, three columns
per trip (flights, hotel, total), ready to open in Excel or Google Sheets and
chart. It is rebuilt from the history on every run, so it can't drift.

Each day shows that day's best complete option, so a trip's three numbers on
one row always describe the same dates and the same hotel.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .config import Config
from .storage import HistoryRow, load_history

SUMMARY_PATH = Path("data/prices-over-time.csv")


def best_by_day(history: list[HistoryRow]) -> dict[date, HistoryRow]:
    """The cheapest complete row per day; an incomplete one only if that's all
    there is, so a day without a hotel price still shows what was found."""
    out: dict[date, HistoryRow] = {}
    for row in history:
        day = row.captured_on
        current = out.get(day)
        if current is None:
            out[day] = row
            continue
        better = (row.is_complete, -row.total) > (current.is_complete, -current.total)
        if better:
            out[day] = row
    return out


def write_summary(config: Config, path: Path = SUMMARY_PATH) -> Path:
    trips = list(config.active_trips)
    by_trip = {t.id: best_by_day(load_history(t.id)) for t in trips}
    days = sorted({d for rows in by_trip.values() for d in rows})

    header = ["date"]
    for trip in trips:
        header += [f"{trip.name} — flights", f"{trip.name} — hotel", f"{trip.name} — total"]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for day in days:
            if not any(
                (row := by_trip[t.id].get(day)) and (row.flight_total or row.lodging_total)
                for t in trips
            ):
                continue      # a day with no flight or hotel price found anywhere
            line = [day.isoformat()]
            for trip in trips:
                row = by_trip[trip.id].get(day)
                if row is None:
                    line += ["", "", ""]
                    continue
                line += [
                    _cell(row.flight_total),
                    _cell(row.lodging_total),
                    _cell(row.total) if row.is_complete else "",
                ]
            writer.writerow(line)
    return path


def _cell(value: float) -> str:
    return f"{value:.0f}" if value else ""
