"""Static dashboard written to docs/ for GitHub Pages.

No build step, no JS dependencies, no hosting bill — one self-contained HTML
file with inline SVG charts, regenerated on every tracking run.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from .config import Config
from .report import (
    NOT_CHECKED_HOTEL,
    OUTLOOK_COLORS,
    TripReport,
    display_notes,
    total_label,
    verdict_label,
    line_chart,
    money,
    trend_lines,
)
from .suggest import Suggestion

OUTPUT = Path("docs/index.html")

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--bg:#0b0f19;--card:#141a26;--ink:#e8eaf0;--muted:#98a1b3;--line:#242c3d;--accent:#6b9bff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding-block:32px;padding-left:20px;padding-right:20px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:14px;margin-bottom:26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px;margin-bottom:18px}
.head{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;justify-content:space-between}
.name{font-size:19px;font-weight:700;margin:0}
.why{color:var(--muted);font-size:13px;margin:4px 0 0;max-width:60ch}
.pill{display:inline-block;padding:3px 11px;border-radius:99px;color:#fff;
font-size:12px;font-weight:600;white-space:nowrap}
.big{font-size:30px;font-weight:800;letter-spacing:-.02em;margin:14px 0 2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:16px}
.stat{border-top:1px solid var(--line);padding-top:9px}
.stat b{display:block;font-size:17px;font-weight:650}
.stat span{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;margin-top:14px;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.chart{margin:14px 0 2px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;
font-size:12px;overflow-x:auto;white-space:pre-wrap}
a{color:var(--accent)}
.outlook{margin:14px 0 0;padding:10px 12px;border-radius:8px;background:var(--bg)}
.outlook b{font-size:15px}
.moves{margin-top:10px;font-size:14px}
.note{color:var(--muted);font-size:13px;margin-top:4px}
.caveat{margin:14px 0 0;padding:8px 11px;border-left:3px solid #d97706;
background:rgba(217,119,6,.09);color:var(--ink);font-size:13px;border-radius:0 6px 6px 0}
footer{color:var(--muted);font-size:12px;margin-top:28px;border-top:1px solid var(--line);padding-top:14px}
"""


def _esc(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _stat(label: str, value: str) -> str:
    return f'<div class="stat"><b>{_esc(value)}</b><span>{_esc(label)}</span></div>'


def _history_table(report: TripReport, limit: int = 14) -> str:
    # Days where nothing was found (fuel alone) are not price history.
    priced = [r for r in report.history if r.flight_total or r.lodging_total]
    rows = priced[-limit:][::-1]
    if not rows:
        return ""
    sym = report.symbol
    body = "".join(
        f"<tr><td>{r.captured_on:%b %d}</td>"
        f"<td>{_esc(r.depart_date.strftime('%b %d'))}–{_esc(r.return_date.strftime('%b %d'))}</td>"
        f'<td class="num">{money(r.flight_total, sym) if r.airfare_applies and r.flight_total else "—"}</td>'
        f'<td class="num">{money(r.lodging_total, sym) if r.lodging_total else "—"}</td>'
        f'<td class="num"><b>{money(r.total, sym)}</b></td>'
        f"<td>{_esc(r.lodging_name[:30])}</td></tr>"
        for r in rows
    )
    return (
        '<div class="scroll"><table><thead><tr><th>Checked</th><th>Travel dates</th>'
        '<th class="num">Flights</th><th class="num">Lodging</th><th class="num">Tracked total</th>'
        "<th>Property</th></tr></thead><tbody>"
        f"{body}</tbody></table></div>"
    )


def _trip_section(report: TripReport) -> str:
    trip, latest = report.trip, report.latest
    header = (
        f'<div class="head"><div><h2 class="name">{_esc(trip.name)}</h2>'
        + (f'<p class="why">{_esc(trip.why)}</p>' if trip.why else "")
        + "</div>"
    )

    if latest is None:
        return (
            f'<section class="card">{header}</div>'
            f'<p class="why">No prices captured yet.</p></section>'
        )

    sym = report.symbol
    assessment = report.total
    verdict = verdict_label(report)
    header += f'<span class="pill" style="background:{report.status_color}">{_esc(verdict)}</span></div>'

    stats = []
    if latest.airfare_applies:
        stats.append(_stat("flights, whole party", money(latest.flight_total, sym)))
    elif latest.travel_mode != "drive":
        stats.append(_stat("flights", "not priced yet"))
    stats.append(_stat(
        f"lodging, {latest.nights} nights",
        money(latest.lodging_total, sym) if latest.lodging_total else NOT_CHECKED_HOTEL,
    ))
    if latest.ground_total:
        stats.append(_stat(trip.ground.notes or "ground transport", money(latest.ground_total, sym)))
    if report.cheapest_ever:
        stats.append(
            _stat(
                f"cheapest ever ({report.cheapest_ever.captured_on:%b %d})",
                money(report.cheapest_ever.total, sym),
            )
        )
    if assessment and assessment.median:
        stats.append(_stat(f"median of {assessment.observations} checks", money(assessment.median, sym)))
    if trip.target_total:
        gap = latest.total - trip.target_total
        stats.append(
            _stat(
                f"vs {money(trip.target_total, sym)} target",
                ("−" if gap < 0 else "+") + money(abs(gap), sym),
            )
        )

    outlook = ""
    if report.outlook is not None:
        color = OUTLOOK_COLORS.get(report.outlook.label, "var(--ink)")
        outlook = (
            f'<div class="outlook" style="border-left:4px solid {color}">'
            f'<b style="color:{color}">{_esc(report.outlook.label)}</b>'
            f'<span class="note"> · {_esc(report.outlook.confidence)} confidence</span>'
            f'<div style="font-size:14px;margin-top:3px">{_esc("; ".join(report.outlook.reasons))}</div>'
            "</div>"
        )
    moves = "".join(f"<div>{_esc(line)}</div>" for line in trend_lines(report))
    notes = "".join(f'<div class="note">{_esc(n)}</div>' for n in display_notes(latest))
    chart = line_chart(
        {
            "Flights": report.flight_series if latest.airfare_applies else [],
            "Hotel": report.lodging_series,
            "Total": report.series,
        },
        currency_symbol=sym,
    )

    return (
        f'<section class="card">{header}'
        f'<div class="big" style="color:{report.status_color}">{money(latest.total, sym)}</div>'
        + (
            f'<div class="note">{_esc(total_label(report))}</div>'
            if total_label(report) != "Total"
            else ""
        )
        + ''
        f'<div class="why">{_esc(latest.destination)} · '
        f"{latest.depart_date:%b %d} – {latest.return_date:%b %d, %Y}"
        + (f" · {_esc(latest.flight_carriers)}" if latest.flight_carriers else "")
        + "</div>"
        + notes
        + outlook
        + (f'<div class="moves">{moves}</div>' if moves else "")
        + f'<div class="chart">{chart}</div>'
        f'<div class="grid">{"".join(stats)}</div>'
        + (
            f'<p class="caveat">{_esc(report.coverage_caveat)}</p>'
            if report.coverage_caveat
            else ""
        )
        + f"{_history_table(report)}"
        + "</section>"
    )


def _suggestions_section(suggestions: list[Suggestion], config: Config) -> str:
    if not suggestions:
        return ""
    from .suggest import as_trip_yaml

    items = []
    for suggestion in suggestions:
        like = config.trip(suggestion.like_trip)
        snippet = (
            as_trip_yaml(suggestion, like, max_drive_hours=config.settings.max_drive_hours)
            if like
            else ""
        )
        items.append(
            f"<li style='margin-bottom:14px'><b>{_esc(suggestion.entry.label)}</b>"
            f"<div class='why'>{_esc(suggestion.summary)}</div>"
            + (
                f"<details><summary style='cursor:pointer;color:var(--accent);font-size:13px;margin-top:6px'>Show config to paste</summary>"
                f"<pre>{_esc(snippet)}</pre></details>"
                if snippet
                else ""
            )
            + "</li>"
        )
    return (
        '<section class="card"><h2 class="name">Worth tracking too?</h2>'
        '<p class="why">Matched on the tags of trips you already track. '
        "Paste a block into <code>config/trips.yaml</code> to start tracking it.</p>"
        f"<ul style='padding-left:18px;margin:12px 0 0'>{''.join(items)}</ul></section>"
    )


def build(
    reports: list[TripReport],
    config: Config,
    suggestions: list[Suggestion] | None = None,
    output: Path = OUTPUT,
    spreadsheet_url: str = "",
) -> Path:
    now = datetime.now(timezone.utc)
    priced = [r for r in reports if r.has_data]
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Vacation Deal Watch</title>"
        f"<style>{CSS}</style></head><body><div class='wrap'>"
        "<h1>Vacation deal watch</h1>"
        f"<p class='sub'>Tracking {len(priced)} trip(s) · "
        f"last checked {now:%b %d, %Y at %H:%M} UTC"
        + (
            f' · <a href="{_esc(spreadsheet_url)}">all prices as a spreadsheet</a>'
            if spreadsheet_url
            else ""
        )
        + "</p>"
        + "".join(_trip_section(r) for r in reports)
        + _suggestions_section(suggestions or [], config)
        + "<footer>Prices are the cheapest option found across the configured dates, "
        "airports and hotels — not a quote. Availability changes faster than this page. "
        "Built from the CSVs in <code>data/history/</code>.</footer>"
        "</div></body></html>"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    return output
