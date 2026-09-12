"""Core value types shared across sources, pricing, storage and reporting."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from typing import Any


def _iso(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class Party:
    """Who is travelling. Children are stored as ages at time of travel."""

    adults: int = 2
    children: tuple[int, ...] = ()

    @property
    def total(self) -> int:
        return self.adults + len(self.children)

    @property
    def infants(self) -> int:
        """Under 2 — priced differently by every airline."""
        return sum(1 for age in self.children if age < 2)

    @property
    def child_seats(self) -> int:
        """Children 2-11, who need a seat but get child pricing."""
        return sum(1 for age in self.children if 2 <= age < 12)

    @property
    def adult_equivalents(self) -> int:
        """Travellers aged 12+, whom airlines price as adults."""
        return self.adults + sum(1 for age in self.children if age >= 12)


@dataclass(frozen=True)
class FlightQuote:
    """One round-trip airfare option for the whole party."""

    origin: str
    destination: str
    depart_date: date
    return_date: date
    total_price: float
    currency: str
    carriers: tuple[str, ...] = ()
    stops_out: int | None = None
    stops_back: int | None = None
    source: str = "unknown"
    deep_link: str = ""

    @property
    def per_person(self) -> float:
        return self.total_price

    def to_dict(self) -> dict[str, Any]:
        return {k: _iso(v) for k, v in dataclasses.asdict(self).items()}


@dataclass(frozen=True)
class LodgingQuote:
    """One lodging option covering the entire stay for the whole party."""

    destination: str
    check_in: date
    check_out: date
    property_name: str
    total_price: float
    currency: str
    property_id: str = ""
    nights: int = 0
    rating: float | None = None
    stars: float | None = None
    board_type: str = ""
    refundable: bool | None = None
    source: str = "unknown"
    deep_link: str = ""

    @property
    def per_night(self) -> float:
        return self.total_price / self.nights if self.nights else self.total_price

    def to_dict(self) -> dict[str, Any]:
        return {k: _iso(v) for k, v in dataclasses.asdict(self).items()}


@dataclass
class TripQuote:
    """A fully costed vacation for one candidate departure/return window."""

    trip_id: str
    destination: str
    depart_date: date
    return_date: date
    nights: int
    currency: str = "USD"
    flight: FlightQuote | None = None
    lodging: LodgingQuote | None = None
    ground_cost: float = 0.0
    ground_notes: str = ""
    travel_mode: str = "air"
    flights_expected: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def flight_cost(self) -> float:
        return self.flight.total_price if self.flight else 0.0

    @property
    def lodging_cost(self) -> float:
        return self.lodging.total_price if self.lodging else 0.0

    @property
    def total_cost(self) -> float:
        return self.flight_cost + self.lodging_cost + self.ground_cost

    @property
    def is_complete(self) -> bool:
        """A quote we can honestly compare against history.

        A driving trip needs no airfare to be complete, and neither does a trip
        whose flights we're deliberately not pricing yet. An air trip that was
        supposed to have a fare and doesn't is incomplete — its total would
        understate the real cost by thousands.
        """
        if self.lodging is None:
            return False
        return self.flight is not None or not self.flights_expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "trip_id": self.trip_id,
            "destination": self.destination,
            "depart_date": self.depart_date.isoformat(),
            "return_date": self.return_date.isoformat(),
            "nights": self.nights,
            "currency": self.currency,
            "flight": self.flight.to_dict() if self.flight else None,
            "lodging": self.lodging.to_dict() if self.lodging else None,
            "ground_cost": round(self.ground_cost, 2),
            "ground_notes": self.ground_notes,
            "travel_mode": self.travel_mode,
            "flights_expected": self.flights_expected,
            "flight_cost": round(self.flight_cost, 2),
            "lodging_cost": round(self.lodging_cost, 2),
            "total_cost": round(self.total_cost, 2),
            "is_complete": self.is_complete,
            "notes": list(self.notes),
        }
