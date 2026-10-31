"""Getting there and moving between bases, with times.

Each plan can have ``plans/travel/<plan>.yaml`` with two lists:

``legs``: the journeys between places (the airport, one base to the next):

    - {day: 7, mode: train, from: "Paris Gare de l'Est", to: Strasbourg,
       depart: "09:25", minutes: 106, note: "TGV, seats reserved"}
    - {day: 1, kind: from_airport, mode: taxi, from: CDG, to: Apartment, minutes: 55}
    - {day: 10, kind: to_airport, mode: train, from: Strasbourg, to: Paris-CDG, minutes: 125}

``timed``: places to be at a set time ("at opening", a booked slot):

    - {day: 6, what: "Disneyland Paris rope drop (opens 9:30)", leave: "07:45",
       arrive: "09:00", how: "RER A from Châtelet"}

Day 1 is the first night's check-in (the morning you land, on a Europe trip);
Day 0 is the flight out. Dates come from the trip's tracked stay, so they
move when the tracked dates do. Airport transfers are timed from the flights:
an hour after landing for passport control and bags, and at the airport three
hours before an international flight home. All times are local and typical,
not a timetable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MODES = {"train", "car", "taxi", "ferry", "flight", "bus", "walk"}
KINDS = {"move", "from_airport", "to_airport"}
CLEAR_ARRIVAL_MIN = 60      # passport control and bags after landing
AIRPORT_BEFORE_MIN = 180    # at the airport before an international flight


class JourneyError(ValueError):
    """A travel file that can't be read as written."""


@dataclass(frozen=True)
class Leg:
    day: int
    mode: str
    origin: str
    dest: str
    minutes: int
    kind: str = "move"
    depart: str = ""        # "HH:MM", local; worked out for airport legs
    note: str = ""


@dataclass(frozen=True)
class Timed:
    day: int
    what: str
    leave: str = ""
    arrive: str = ""
    how: str = ""
    closed: tuple[str, ...] = ()   # weekdays it's shut: ("Mon",), ("Tue",)


def travel_path(plan: Path) -> Path:
    return plan.parent / "travel" / f"{plan.stem}.yaml"


def load(path: Path, strict: bool = False) -> tuple[list[Leg], list[Timed]]:
    if not path.is_file():
        return [], []
    raw = yaml.safe_load(path.read_text()) or {}
    legs, timed = [], []
    for i, item in enumerate(raw.get("legs") or []):
        try:
            legs.append(_leg(item))
        except JourneyError as exc:
            if strict:
                raise JourneyError(f"{path.name} leg {i + 1}: {exc}") from exc
            log.warning("%s leg %d skipped: %s", path.name, i + 1, exc)
    for i, item in enumerate(raw.get("timed") or []):
        try:
            timed.append(_timed(item))
        except JourneyError as exc:
            if strict:
                raise JourneyError(f"{path.name} timed {i + 1}: {exc}") from exc
            log.warning("%s timed %d skipped: %s", path.name, i + 1, exc)
    return legs, timed


def _clock(value: object, field: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise JourneyError(f"'{field}' must be HH:MM, got {text!r}") from exc
    return text


def _leg(item: object) -> Leg:
    if not isinstance(item, dict):
        raise JourneyError("each leg must be a mapping")
    extra = set(item) - {"day", "mode", "from", "to", "minutes", "kind", "depart", "note"}
    if extra:
        raise JourneyError(f"unknown key(s) {sorted(map(str, extra))}")
    mode, kind = str(item.get("mode", "")), str(item.get("kind", "move"))
    if mode not in MODES:
        raise JourneyError(f"mode must be one of {sorted(MODES)}")
    if kind not in KINDS:
        raise JourneyError(f"kind must be one of {sorted(KINDS)}")
    if not isinstance(item.get("day"), int) or not isinstance(item.get("minutes"), int):
        raise JourneyError("'day' and 'minutes' must be whole numbers")
    return Leg(item["day"], mode, str(item.get("from", "")), str(item.get("to", "")),
               item["minutes"], kind, _clock(item.get("depart"), "depart"), str(item.get("note", "")))


def _timed(item: object) -> Timed:
    if not isinstance(item, dict):
        raise JourneyError("each timed entry must be a mapping")
    extra = set(item) - {"day", "what", "leave", "arrive", "how", "closed"}
    if extra:
        raise JourneyError(f"unknown key(s) {sorted(map(str, extra))}")
    if not isinstance(item.get("day"), int) or not item.get("what"):
        raise JourneyError("'day' and 'what' are required")
    closed = tuple(str(d)[:3].title() for d in item.get("closed") or ())
    if any(d not in WEEKDAYS for d in closed):
        raise JourneyError(f"'closed' takes weekdays like Mon, Tue; got {list(closed)}")
    return Timed(item["day"], str(item["what"]), _clock(item.get("leave"), "leave"),
                 _clock(item.get("arrive"), "arrive"), str(item.get("how", "")), closed)


# --------------------------------------------------------------------------- #
# Times
# --------------------------------------------------------------------------- #

def day_date(check_in: date, day: int) -> date:
    return check_in + timedelta(days=day - 1)


def at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time())


def short(when: datetime) -> str:
    """'8:45p'."""
    hour = when.hour % 12 or 12
    return f"{hour}:{when:%M}{'a' if when.hour < 12 else 'p'}"


def stamp(when: datetime) -> str:
    """'Wed Jul 7 8:45p'."""
    return f"{when:%a %b} {when.day} {short(when)}"


def span(start: datetime, end: datetime) -> str:
    """'Wed Jul 7 8:45p – Thu Jul 8 4:15p', or '… 9:25a – 11:11a' on one day."""
    if start.date() == end.date():
        return f"{stamp(start)} – {short(end)}"
    return f"{stamp(start)} – {stamp(end)}"


def hours(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def leg_times(
    leg: Leg, check_in: date, landed: datetime | None, home_flight: datetime | None
) -> tuple[datetime | None, datetime | None, str]:
    """(start, end, how it was worked out) for one leg."""
    day = day_date(check_in, leg.day)
    if leg.kind == "from_airport" and landed is not None:
        start = landed + timedelta(minutes=CLEAR_ARRIVAL_MIN)
        return start, start + timedelta(minutes=leg.minutes), "an hour after landing"
    if leg.kind == "to_airport" and home_flight is not None:
        end = home_flight - timedelta(minutes=AIRPORT_BEFORE_MIN)
        return end - timedelta(minutes=leg.minutes), end, "at the airport 3 h before the flight"
    if leg.depart:
        start = at(day, leg.depart)
        return start, start + timedelta(minutes=leg.minutes), ""
    return None, None, ""


def clash(item: Timed, check_in: date | None) -> str:
    """A warning when the tracked dates put this on a day it's closed."""
    if not (check_in and item.closed):
        return ""
    day = day_date(check_in, item.day)
    name = WEEKDAYS[day.weekday()]
    if name not in item.closed:
        return ""
    return (f"Closed on {name}days, and Day {item.day} falls on {day:%A, %b} {day.day} for "
            "these dates: swap it with another day.")


def timed_line(item: Timed, check_in: date | None) -> str:
    """'Leave 7:45a (RER A from Châtelet) · arrive 9:00a · Disneyland rope drop'."""
    parts = []
    if item.leave:
        clock = short(at(date(2000, 1, 1), item.leave))
        parts.append(f"Leave {clock}" + (f" ({item.how})" if item.how else ""))
    elif item.how:
        parts.append(item.how)
    if item.arrive:
        parts.append(f"arrive {short(at(date(2000, 1, 1), item.arrive))}")
    parts.append(item.what)
    when = f"{day_date(check_in, item.day):%a %b} {day_date(check_in, item.day).day}: " if check_in else ""
    return when + " · ".join(parts)
