"""Assembling what both the email digest and the dashboard need to say."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Config, Trip
from .forecast import Outlook, outlook
from .insights import FlightInsight, load_insights
from .stats import PriceAssessment, Trend, assess, best_ever, daily_series, trend
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
    flight_series: list[tuple[date, float]] = field(default_factory=list)
    lodging_series: list[tuple[date, float]] = field(default_factory=list)
    total_trend: Trend | None = None
    flight_trend: Trend | None = None
    lodging_trend: Trend | None = None
    insight: FlightInsight | None = None
    outlook: Outlook | None = None

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


def build_report(
    trip: Trip,
    config: Config | None = None,
    insight: FlightInsight | None = None,
    today: date | None = None,
) -> TripReport:
    history = load_history(trip.id)
    latest = history[-1] if history else None
    series = daily_series(history, "total")
    flight_series = daily_series(history, "flight_total")
    lodging_series = daily_series(history, "lodging_total")
    report = TripReport(
        trip=trip,
        history=history,
        latest=latest,
        total=assess(history, field="total"),
        flight=assess(history, field="flight_total"),
        lodging=assess(history, field="lodging_total"),
        cheapest_ever=best_ever(history, "total"),
        series=series,
        flight_series=flight_series,
        lodging_series=lodging_series,
        total_trend=trend(series),
        flight_trend=trend(flight_series),
        lodging_trend=trend(lodging_series),
        insight=insight,
    )
    if config is not None:
        today = today or date.today()
        days = len({d for d, _ in lodging_series} | {d for d, _ in flight_series})
        report.outlook = outlook(
            trip,
            config.settings,
            today,
            latest.depart_date if latest else None,
            flight_price=latest.flight_total if latest and latest.flight_total else None,
            insight=insight,
            flight_trend=report.flight_trend,
            lodging_trend=report.lodging_trend,
            days_of_history=days,
            currency_symbol=report.symbol,
        )
    return report


def build_reports(config: Config, today: date | None = None) -> list[TripReport]:
    insights = load_insights()
    return [
        build_report(trip, config, insights.get(trip.id), today)
        for trip in config.active_trips
    ]


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


OUTLOOK_COLORS = {
    "Good time to book": "#15803d",
    "Book soon": "#b45309",
    "Keep watching": "#2563eb",
    "Wait if you can": "#6b7280",
}

# Notes that explain a price to a person, as opposed to plumbing.
_PLUMBING = ("hotel prices unavailable", "travelpayouts")


def display_notes(row: HistoryRow | None) -> list[str]:
    if row is None or not row.notes:
        return []
    seen, out = set(), []
    for note in (n.strip() for n in row.notes.split("|")):
        if note and not note.lower().startswith(_PLUMBING) and note not in seen:
            seen.add(note)
            out.append(note)
    return out


def trend_lines(report: TripReport) -> list[str]:
    """'Flights $8,120 — up about $90/week (1.1%) over 21 days' and friends."""
    sym, latest = report.symbol, report.latest
    if latest is None:
        return []
    lines = []
    if latest.airfare_applies and latest.flight_total:
        text = f"Flights {money(latest.flight_total, sym)}"
        if report.flight_trend:
            text += f" — {report.flight_trend.phrase(sym)}"
        lines.append(text)
    if latest.lodging_total:
        text = f"Hotel {money(latest.lodging_total, sym)}"
        if report.lodging_trend:
            text += f" — {report.lodging_trend.phrase(sym)}"
        lines.append(text)
    if report.total_trend and len(lines) > 1:
        lines.append(f"Total — {report.total_trend.phrase(sym)}")
    return lines


SERIES_COLORS = {"Flights": "#2563eb", "Hotel": "#d97706", "Total": "#6d28d9"}


def line_chart(
    named: dict[str, list[tuple[date, float]]],
    width: int = 640,
    height: int = 170,
    currency_symbol: str = "$",
) -> str:
    """Price over time, one line per component, on a shared dollar axis.

    Inline SVG so the dashboard needs no scripts. Lines with fewer than two
    points are left out rather than drawn as a misleading dot.
    """
    named = {k: v for k, v in named.items() if len(v) >= 2}
    if not named:
        return (
            '<p class="why">The chart fills in once there are two days of prices.</p>'
        )
    all_days = sorted({d for series in named.values() for d, _ in series})
    all_vals = [v for series in named.values() for _, v in series]
    first, last = all_days[0], all_days[-1]
    low, high = min(all_vals), max(all_vals)
    pad_v = (high - low) * 0.08 or high * 0.05 or 1.0
    low, high = max(0.0, low - pad_v), high + pad_v
    left, right, top, bottom = 58, 10, 10, 24
    plot_w, plot_h = width - left - right, height - top - bottom
    span_days = max(1, (last - first).days)

    def x(day: date) -> float:
        return left + (day - first).days / span_days * plot_w

    def y(value: float) -> float:
        return top + plot_h - (value - low) / (high - low) * plot_h

    grid = []
    for i in range(4):
        value = low + (high - low) * i / 3
        gy = y(value)
        grid.append(
            f'<line x1="{left}" x2="{width - right}" y1="{gy:.1f}" y2="{gy:.1f}" '
            f'stroke="currentColor" stroke-opacity="0.12"/>'
            f'<text x="{left - 6}" y="{gy + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="currentColor" fill-opacity="0.6">{currency_symbol}{value:,.0f}</text>'
        )
    labels = (
        f'<text x="{left}" y="{height - 6}" font-size="11" fill="currentColor" '
        f'fill-opacity="0.6">{first:%b %d}</text>'
        f'<text x="{width - right}" y="{height - 6}" text-anchor="end" font-size="11" '
        f'fill="currentColor" fill-opacity="0.6">{last:%b %d}</text>'
    )
    lines = []
    for name, series in named.items():
        color = SERIES_COLORS.get(name, "#111827")
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{x(d):.1f},{y(v):.1f}" for i, (d, v) in enumerate(series)
        )
        lx, ly = x(series[-1][0]), y(series[-1][1])
        lines.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{color}"/>'
        )
    legend = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px">'
        f'<span style="width:12px;height:3px;border-radius:2px;background:{SERIES_COLORS.get(n, "#111827")}"></span>'
        f"{n}</span>"
        for n in named
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="Price history {first} to {last}" style="max-width:{width}px;display:block">'
        + "".join(grid) + labels + "".join(lines)
        + f'</svg><div style="font-size:12px;margin-top:4px">{legend}</div>'
    )
