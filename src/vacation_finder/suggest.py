"""Proposing trips you haven't thought of, scored against your stated parameters.

Matching is deliberately rule-based rather than clever: it reads the tags and
the "why" you wrote for the trips you already track, and finds catalog entries
that share them, are in season for your travel window, and are reachable from
your home airports. Suggestions carry a reason, so you can tell whether the
match is real or coincidental.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .config import Config, Trip

CATALOG_PATH = Path("config/destinations.yaml")


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    label: str
    tags: tuple[str, ...] = ()
    best_months: tuple[int, ...] = ()
    city_code: str = ""
    why: str = ""
    region: str = ""

    @property
    def hotel_city(self) -> str:
        return self.city_code or self.code


@dataclass
class Suggestion:
    entry: CatalogEntry
    score: float
    reasons: list[str] = field(default_factory=list)
    like_trip: str = ""
    estimated_total: float | None = None

    @property
    def summary(self) -> str:
        return "; ".join(self.reasons)


def load_catalog(path: Path = CATALOG_PATH) -> list[CatalogEntry]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or []
    entries = raw.get("destinations", raw) if isinstance(raw, dict) else raw
    out: list[CatalogEntry] = []
    for item in entries or []:
        if not isinstance(item, dict) or "code" not in item:
            continue
        out.append(
            CatalogEntry(
                code=str(item["code"]).strip().upper(),
                label=str(item.get("label", item["code"])),
                tags=tuple(str(t).strip().lower() for t in item.get("tags", []) or []),
                best_months=tuple(int(m) for m in item.get("best_months", []) or []),
                city_code=str(item.get("city_code", "")).strip().upper(),
                why=str(item.get("why", "")),
                region=str(item.get("region", "")),
            )
        )
    return out


def _trip_months(trip: Trip) -> set[int]:
    window = trip.dates
    if window.mode == "fixed" and window.depart:
        return {window.depart.month}
    if not (window.earliest and window.latest):
        return set()
    months: set[int] = set()
    year, month = window.earliest.year, window.earliest.month
    while (year, month) <= (window.latest.year, window.latest.month):
        months.add(month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def tracked_codes(config: Config) -> set[str]:
    return {d.code for trip in config.trips for d in trip.destinations}


def suggest_for_trip(
    trip: Trip, catalog: list[CatalogEntry], exclude: set[str], limit: int = 3
) -> list[Suggestion]:
    """Catalog entries resembling one tracked trip, best match first."""
    trip_tags = set(trip.tags)
    months = _trip_months(trip)
    scored: list[Suggestion] = []

    for entry in catalog:
        if entry.code in exclude:
            continue
        shared = trip_tags & set(entry.tags)
        if not shared:
            continue

        reasons = [f"shares {', '.join(sorted(shared))} with {trip.name}"]
        score = float(len(shared))

        if months and entry.best_months:
            if months & set(entry.best_months):
                score += 1.5
                reasons.append("in season for your dates")
            else:
                score -= 1.5
                reasons.append("off-season for your dates")

        if entry.why:
            reasons.append(entry.why)

        scored.append(Suggestion(entry=entry, score=score, reasons=reasons, like_trip=trip.id))

    scored.sort(key=lambda s: (-s.score, s.entry.label))
    return scored[:limit]


def suggest(
    config: Config, catalog: list[CatalogEntry] | None = None, limit: int = 4
) -> list[Suggestion]:
    """Best suggestions across every active trip, de-duplicated by destination."""
    catalog = catalog if catalog is not None else load_catalog()
    exclude = tracked_codes(config)
    best_by_code: dict[str, Suggestion] = {}

    for trip in config.active_trips:
        for suggestion in suggest_for_trip(trip, catalog, exclude, limit=limit):
            existing = best_by_code.get(suggestion.entry.code)
            if existing is None or suggestion.score > existing.score:
                best_by_code[suggestion.entry.code] = suggestion

    ranked = sorted(best_by_code.values(), key=lambda s: (-s.score, s.entry.label))
    return ranked[:limit]


def as_trip_yaml(suggestion: Suggestion, like: Trip, today: date | None = None) -> str:
    """A ready-to-paste trips.yaml block, so accepting a suggestion is a copy job."""
    entry = suggestion.entry
    trip_id = entry.label.split(",")[0].strip().lower().replace(" ", "-").replace("/", "-")
    trip_id = "".join(ch for ch in trip_id if ch.isalnum() or ch in "-_") or entry.code.lower()
    window = like.dates
    dates_block = (
        f"      mode: fixed\n"
        f"      depart: {window.depart}\n"
        f"      return: {window.return_}"
        if window.mode == "fixed"
        else (
            f"      mode: window\n"
            f"      earliest: {window.earliest}\n"
            f"      latest: {window.latest}\n"
            f"      nights: {window.nights}"
        )
    )
    children = ", ".join(str(age) for age in like.party.children)
    return (
        f"  - id: {trip_id}\n"
        f"    name: \"{entry.label}\"\n"
        f"    why: \"{entry.why}\"\n"
        f"    active: true\n"
        f"    destinations:\n"
        f"      - code: {entry.code}\n"
        f"        label: \"{entry.label}\"\n"
        f"        city_code: {entry.hotel_city}\n"
        f"    party: {{adults: {like.party.adults}, children: [{children}]}}\n"
        f"    dates:\n{dates_block}\n"
        f"    tags: [{', '.join(entry.tags)}]\n"
    )
