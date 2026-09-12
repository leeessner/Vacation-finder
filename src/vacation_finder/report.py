"""Assembling what both the email digest and the dashboard need to say."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Config, Trip
from .stats import PriceAssessment, assess, best_ever, daily_series
from .storage import HistoryRow, load_history

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "MXN": "MX$"}


def symbol(currency: str) -> str:
    return CURRENCY_SYMBOLS.get(currency.upper(), f"{currency.upper()} ")


@dataclass
class TripReport:
    """One trip's current state, ready to render."""

    trip: Trip
    history: list[HistoryRow] = field(default_factory=list)
    latest: HistoryRow | None = None
    total: PriceAssessment | None = None
    flight: PriceAssessment | None = None
    lodging: PriceAssessment | None = None
    cheapest_ever: HistoryRow | None = None
    series: list[tuple[date, float]] = field(default_factory=list)

    @property
    def currency(self) -> str:
        return self.latest.currency if self.latest else "USD"

    @property
    def symbol(self) -> str:
        return symbol(self.currency)

    @property
    def has_data(self) -> bool:
        return self.latest is not None

    @property
    def coverage_caveat(self) -> str:
        """Plain warning when the lodging number is weaker than it looks."""
        if not self.trip.lodging.enabled:
            return "Lodging isn't tracked for this trip — price it yourself."
        if self.trip.lodging.coverage == "thin":
            return (
                "Lodging coverage here is thin (family rooms, small independents). "
                "Treat this as a floor, not a quote."
            )
        return ""

    @property
    def vs_target(self) -> float | None:
        """How far above (+) or below (-) the target total we are, in currency."""
        if self.latest is None or self.trip.target_total is None:
            return None
        return self.latest.total - self.trip.target_total

    @property
    def status_color(self) -> str:
        """Traffic light used consistently by the email and the dashboard."""
        if self.total is None:
            return "#6b7280"
        if self.total.is_all_time_low or self.total.verdict == "excellent":
            return "#15803d"
        if self.total.verdict == "good":
            return "#4d7c0f"
        if self.total.verdict == "typical":
            return "#a16207"
        return "#b91c1c"


def build_report(trip: Trip) -> TripReport:
    history = load_history(trip.id)
    return TripReport(
        trip=trip,
        history=history,
        latest=history[-1] if history else None,
        total=assess(history, field="total"),
        flight=assess(history, field="flight_total"),
        lodging=assess(history, field="lodging_total"),
        cheapest_ever=best_ever(history, "total"),
        series=daily_series(history, "total"),
    )


def build_reports(config: Config) -> list[TripReport]:
    return [build_report(trip) for trip in config.active_trips]


def sparkline(
    series: list[tuple[date, float]],
    width: int = 260,
    height: int = 56,
    color: str = "#2563eb",
) -> str:
    """Inline SVG price history. Inline so it renders in email without hosting."""
    points = [value for _, value in series]
    if len(points) < 2:
        return (
            f'<svg width="{width}" height="{height}" role="img" '
            f'aria-label="Not enough history to chart yet"></svg>'
        )

    low, high = min(points), max(points)
    span = (high - low) or 1.0
    pad = 4
    usable_h = height - 2 * pad
    step = (width - 2 * pad) / (len(points) - 1)

    coords = [
        (pad + i * step, pad + usable_h - ((v - low) / span) * usable_h)
        for i, v in enumerate(points)
    ]
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords)
    )
    area = (
        f"M{coords[0][0]:.1f},{height - pad:.1f} "
        + " ".join(f"L{x:.1f},{y:.1f}" for x, y in coords)
        + f" L{coords[-1][0]:.1f},{height - pad:.1f} Z"
    )
    last_x, last_y = coords[-1]
    lowest_i = min(range(len(points)), key=lambda i: points[i])
    low_x, low_y = coords[lowest_i]

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="Price history from {series[0][0]} to {series[-1][0]}, '
        f'low {low:.0f}, high {high:.0f}">'
        f'<path d="{area}" fill="{color}" fill-opacity="0.10"/>'
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{low_x:.1f}" cy="{low_y:.1f}" r="3" fill="#15803d"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" fill="{color}"/>'
        f"</svg>"
    )


def money(value: float | None, currency_symbol: str = "$") -> str:
    if value is None:
        return "—"
    return f"{currency_symbol}{value:,.0f}"


def delta_phrase(assessment: PriceAssessment | None, currency_symbol: str = "$") -> str:
    """'down $210 since yesterday' / 'up $95 since yesterday' / 'unchanged'."""
    if assessment is None or assessment.delta_vs_previous is None:
        return ""
    delta = assessment.delta_vs_previous
    if abs(delta) < 1:
        return "unchanged since last check"
    direction = "down" if delta < 0 else "up"
    return f"{direction} {currency_symbol}{abs(delta):,.0f} since last check"
