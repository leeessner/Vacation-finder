"""Static dashboard written to docs/ for GitHub Pages.

No build step, no JS, no hosting bill: plain HTML files with inline SVG
charts, regenerated on every tracking run.

- ``docs/index.html`` is the highlights page: a tab per trip across the top
  (nights and total), then one card per trip with the price, the outlook and
  a flights-and-hotel chart. Nothing else.
- ``docs/trips/<id>.html`` is one page per trip: the full tracker detail
  (outlook reasons, trends, stats, history) followed by the trip's plan from
  ``plans/*.md`` (day by day, food, fixed costs, when to book, sources).
- Plans with no tracked trip yet (and the resort list) get a page too, so
  every plan is one click from the home page.
- ``docs/ideas.html`` holds the "worth tracking too?" suggestions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import markdown

from . import itinerary_map
from .config import Config
from .report import (
    NOT_CHECKED_HOTEL,
    OUTLOOK_COLORS,
    TripReport,
    display_notes,
    line_chart,
    money,
    total_label,
    trend_lines,
    verdict_label,
)
from .suggest import Suggestion

OUTPUT = Path("docs/index.html")
PLANS_DIR = Path("plans")
# Reference notes in plans/ that aren't trip plans.
NOT_PLANS = {"family-profile.md", "inspiration.md"}

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=Libre+Caslon+Text:wght@400;700&family=Public+Sans:ital,wght@0,400;0,600;0,700;1,400"
    '&display=swap">'
)

CSS = """
:root{--paper:#f7f8fb;--card:#fff;--ink:#1b2233;--muted:#5a6478;--line:#dfe3ec;
--accent:#2b4c9b;--accent-soft:#e7ecf8;--check-bg:#fff3d6;--check-fg:#7a5200;
--display:"Libre Caslon Text",Georgia,"Times New Roman",serif;
--body:"Public Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--paper:#12161f;--card:#1a2030;--ink:#e6e9f0;--muted:#9aa3b6;--line:#2c3446;
--accent:#8fa9ec;--accent-soft:#222c46;--check-bg:#3a2e12;--check-fg:#f3d27a;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15.5px/1.6 var(--body)}
.wrap{max-width:1000px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px))}
header.top{padding-block:26px 16px}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
font-weight:600;margin:0 0 4px}
header.top h1{font:700 clamp(26px,4.6vw,36px)/1.15 var(--display);margin:0}
.sub{color:var(--muted);font-size:14px;margin:6px 0 0}
nav.tabs{position:sticky;top:0;z-index:5;background:var(--card);border-block:1px solid var(--line)}
nav.tabs .wrap{display:flex;gap:4px;overflow-x:auto;padding-block:6px}
.tab{flex:0 0 auto;display:flex;flex-direction:column;padding:7px 13px;border-radius:6px 6px 0 0;
text-decoration:none;color:var(--muted);border-bottom:3px solid transparent}
.tab:hover{background:var(--accent-soft);color:var(--ink)}
.tab:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tab[aria-current=page]{color:var(--ink);border-bottom-color:var(--accent)}
.tab b{font-size:14.5px}
.tab span{font-size:12px;font-variant-numeric:tabular-nums}
main{padding-block:24px 56px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.tcard{display:flex;flex-direction:column;gap:8px;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:16px 18px;color:inherit;text-decoration:none;min-width:0}
.tcard:hover{border-color:var(--accent)}
.tcard:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tcard h2{font:700 19px/1.25 var(--display);margin:0}
.price{font-size:28px;font-weight:700;letter-spacing:-.01em;font-variant-numeric:tabular-nums;line-height:1.1}
.price small{display:block;font-size:12.5px;font-weight:400;color:var(--muted);letter-spacing:0;margin-top:3px}
.pill{align-self:flex-start;display:inline-block;padding:3px 11px;border-radius:99px;color:#fff;
font-size:12.5px;font-weight:600;white-space:nowrap}
.status{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.conf{font-size:12.5px;color:var(--muted)}
.chart{margin-top:4px;color:var(--ink)}
.more{margin-top:auto;font-size:13px;color:var(--accent);font-weight:600}
.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin-bottom:18px}
.head{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;justify-content:space-between}
.name{font:700 22px/1.25 var(--display);margin:0}
.why{color:var(--muted);font-size:13.5px;margin:4px 0 0;max-width:65ch}
.big{font-size:30px;font-weight:700;margin:14px 0 2px;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:16px}
.stat{border-top:1px solid var(--line);padding-top:9px}
.stat b{display:block;font-size:17px;font-weight:650;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:12px}
.outlook{margin:14px 0 0;padding:10px 12px;border-radius:8px;background:var(--paper)}
.outlook b{font-size:15px}
.moves{margin-top:10px;font-size:14px}
.note{color:var(--muted);font-size:13px;margin-top:4px}
.caveat{margin:14px 0 0;padding:8px 11px;border-left:3px solid #d97706;
background:rgba(217,119,6,.09);font-size:13px;border-radius:0 6px 6px 0}
.scroll{overflow-x:auto;margin:14px 0;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 11px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.05em;
background:var(--accent-soft)}
tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:var(--accent);text-underline-offset:2px}
.plan h1{font:700 clamp(24px,4.2vw,32px)/1.2 var(--display);margin:34px 0 12px;text-wrap:balance}
.plan h2{font:700 21px/1.3 var(--display);margin:36px 0 10px;padding-top:18px;border-top:1px solid var(--line)}
.plan h3{font:700 17px/1.35 var(--display);margin:24px 0 8px;color:var(--accent)}
.plan p,.plan li{max-width:70ch}
.plan hr{display:none}
.plan blockquote{margin:18px 0;padding:12px 16px;background:var(--check-bg);border-radius:6px}
.plan blockquote p{margin:0}
.plan p.day{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:13px 16px;margin:12px 0}
.plan p.day strong:first-child{display:block;font:700 16.5px/1.35 var(--display);color:var(--accent);margin-bottom:3px}
.check{display:inline-block;font-size:11.5px;font-weight:600;letter-spacing:.04em;padding:0 6px;
border-radius:4px;background:var(--check-bg);color:var(--check-fg);text-transform:uppercase;line-height:1.6}
.section-label{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
font-weight:600;margin:0 0 10px}
pre{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:10px;
font-size:12px;overflow-x:auto;white-space:pre-wrap}
footer{color:var(--muted);font-size:12.5px;margin-top:30px;border-top:1px solid var(--line);padding-top:14px}
"""


def _esc(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #

@dataclass
class Plan:
    path: Path
    title: str
    html: str
    nights: str        # "8 nights", or "" when the plan doesn't say
    places: tuple = ()  # itinerary_map.Place pins from plans/maps/<plan>.yaml


def render_plan(path: Path) -> Plan:
    """A plan file as HTML, with its title, nights and day entries pulled out."""
    text = path.read_text()
    match = re.search(r"^# (.+)$", text, re.M)
    title = match.group(1) if match else path.stem.replace("-", " ").title()
    title = re.sub(r"\s+—\s+[^—]*draft plan.*$", "", title)
    body = re.sub(r"^# .+\n", "", text, count=1)
    nights = re.search(r"\*\*Shape:\*\*\s*(\d+ nights)", text)

    html = markdown.markdown(body, extensions=["tables", "sane_lists"])
    html = html.replace("<table>", '<div class="scroll"><table>').replace(
        "</table>", "</table></div>"
    )
    html = html.replace("<em>check</em>", '<span class="check">check</span>')
    html = re.sub(
        r"<p><strong>(Days? \d+[^<]*)</strong>", r'<p class="day"><strong>\1</strong>', html
    )
    html = re.sub(
        r'<a href="(https?://[^"]+)"', r'<a href="\1" target="_blank" rel="noopener"', html
    )
    places = tuple(itinerary_map.load_places(itinerary_map.map_path(path)))
    return Plan(path, title, html, nights.group(1) if nights else "", places)


def _plan_files(plans_dir: Path) -> list[Path]:
    if not plans_dir.is_dir():
        return []
    return sorted(p for p in plans_dir.glob("*.md") if p.name not in NOT_PLANS)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

@dataclass
class Page:
    slug: str
    name: str
    meta: str                         # tab subtitle: "9 nights · $14,230"
    report: TripReport | None = None
    plan: Plan | None = None

    @property
    def href(self) -> str:            # relative to docs/
        return f"trips/{self.slug}.html"


def _tab_meta(report: TripReport) -> str:
    latest, trip = report.latest, report.trip
    if latest is None:
        return f"{trip.dates.nights} nights · no price yet"
    price = money(latest.total, report.symbol)
    return f"{latest.nights} nights · {price}" + (
        "" if total_label(report) == "Total" else " so far"
    )


def _pages(reports: list[TripReport], plans_dir: Path) -> list[Page]:
    pages: list[Page] = []
    used: set[Path] = set()
    for report in reports:
        plan = None
        if report.trip.plan:
            candidate = Path(report.trip.plan)
            if not candidate.is_absolute():
                candidate = plans_dir.parent / candidate
            if candidate.is_file():
                plan = render_plan(candidate)
                used.add(candidate.resolve())
        pages.append(Page(report.trip.id, report.trip.name, _tab_meta(report), report, plan))

    for path in _plan_files(plans_dir):
        if path.resolve() in used:
            continue
        plan = render_plan(path)
        meta = f"{plan.nights} · not tracked yet" if plan.nights else "plan"
        pages.append(Page(f"plan-{path.stem}", plan.title, meta, None, plan))
    return pages


def _tabs(pages: list[Page], current: str, prefix: str) -> str:
    home = (
        f'<a class="tab" href="{prefix}index.html"'
        + (' aria-current="page"' if current == "home" else "")
        + '><b>All trips</b><span>highlights</span></a>'
    )
    return (
        '<nav class="tabs" aria-label="Trips"><div class="wrap">'
        + home
        + "".join(
            f'<a class="tab" href="{prefix}{_esc(p.href)}"'
            + (' aria-current="page"' if p.slug == current else "")
            + f"><b>{_esc(p.name)}</b><span>{_esc(p.meta)}</span></a>"
            for p in pages
        )
        + "</div></nav>"
    )


def _document(
    title: str, header: str, tabs: str, body: str, footer: str, with_map: bool = False
) -> str:
    head = itinerary_map.HEAD if with_map else ""
    css = CSS + (itinerary_map.CSS if with_map else "")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)}</title>{FONTS}{head}<style>{css}</style></head><body>"
        f"<header class='top'><div class='wrap'>{header}</div></header>"
        f"{tabs}<main><div class='wrap'>{body}<footer>{footer}</footer></div></main>"
        + (itinerary_map.SCRIPT if with_map else "")
        + "</body></html>"
    )


# --------------------------------------------------------------------------- #
# Home: one card per trip — price, outlook, chart
# --------------------------------------------------------------------------- #

def _card(page: Page) -> str:
    report = page.report
    if report is None:
        return (
            f'<a class="tcard" href="{_esc(page.href)}"><h2>{_esc(page.name)}</h2>'
            f'<div class="muted">Plan ready · not tracked yet'
            + (f" · {_esc(page.plan.nights)}" if page.plan and page.plan.nights else "")
            + '</div><span class="more">Open the plan →</span></a>'
        )
    latest = report.latest
    if latest is None:
        return (
            f'<a class="tcard" href="{_esc(page.href)}"><h2>{_esc(page.name)}</h2>'
            '<div class="muted">No prices yet</div><span class="more">Details →</span></a>'
        )
    label = total_label(report)
    price = (
        f'<div class="price">{money(latest.total, report.symbol)}'
        f"<small>{latest.nights} nights · {latest.depart_date:%b %d} – {latest.return_date:%b %d}"
        + ("" if label == "Total" else f" · {_esc(label.lower())}")
        + "</small></div>"
    )
    pill = ""
    if report.outlook is not None:
        color = OUTLOOK_COLORS.get(report.outlook.label, "#5a6478")
        pill = (
            f'<span class="pill" style="background:{color}">{_esc(report.outlook.label)}</span>'
            f'<span class="conf">{_esc(report.outlook.confidence)} confidence</span>'
        )
    chart = line_chart(
        {
            "Flights": report.flight_series if latest.airfare_applies else [],
            "Hotel": report.lodging_series,
        },
        width=360,
        height=150,
        currency_symbol=report.symbol,
    )
    return (
        f'<a class="tcard" href="{_esc(page.href)}"><h2>{_esc(page.name)}</h2>'
        f"{price}<div class='status'>{pill}</div><div class='chart'>{chart}</div>"
        + f'<span class="more">{"Details and plan" if page.plan else "Details"} →</span></a>'
    )


# --------------------------------------------------------------------------- #
# Trip page: the full tracker detail, then the plan
# --------------------------------------------------------------------------- #

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


def _tracker_section(report: TripReport) -> str:
    trip, latest = report.trip, report.latest
    header = (
        f'<div class="head"><div><h2 class="name">Price tracker</h2>'
        + (f'<p class="why">{_esc(trip.why)}</p>' if trip.why else "")
        + "</div>"
    )
    if latest is None:
        return f'<section class="card">{header}</div><p class="why">No prices captured yet.</p></section>'

    sym = report.symbol
    assessment = report.total
    header += (
        f'<span class="pill" style="background:{report.status_color}">'
        f"{_esc(verdict_label(report))}</span></div>"
    )

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
        stats.append(_stat(
            f"cheapest ever ({report.cheapest_ever.captured_on:%b %d})",
            money(report.cheapest_ever.total, sym),
        ))
    if assessment and assessment.median:
        stats.append(_stat(f"median of {assessment.observations} checks", money(assessment.median, sym)))
    if trip.target_total:
        gap = latest.total - trip.target_total
        stats.append(_stat(
            f"vs {money(trip.target_total, sym)} target",
            ("−" if gap < 0 else "+") + money(abs(gap), sym),
        ))

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
    label = total_label(report)
    return (
        f'<section class="card">{header}'
        f'<div class="big">{money(latest.total, sym)}</div>'
        + (f'<div class="note">{_esc(label)}</div>' if label != "Total" else "")
        + f'<div class="why">{_esc(latest.destination)} · '
        f"{latest.depart_date:%b %d} – {latest.return_date:%b %d, %Y}"
        + (f" · {_esc(latest.flight_carriers)}" if latest.flight_carriers else "")
        + "</div>"
        + notes
        + outlook
        + (f'<div class="moves">{moves}</div>' if moves else "")
        + f'<div class="chart">{chart}</div>'
        f'<div class="grid">{"".join(stats)}</div>'
        + (f'<p class="caveat">{_esc(report.coverage_caveat)}</p>' if report.coverage_caveat else "")
        + _history_table(report)
        + "</section>"
    )


def _trip_page(page: Page, pages: list[Page], stamp: str, footer: str) -> str:
    header = f'<p class="eyebrow"><a href="../index.html">Vacation deal watch</a></p><h1>{_esc(page.name)}</h1>'
    body = ""
    if page.report is not None:
        body += _tracker_section(page.report)
    else:
        body += '<p class="muted">This plan isn\'t tracked yet: no daily flight or hotel prices.</p>'
    if page.plan is not None:
        # A plan-only page already carries the plan's title in its header.
        heading = "" if page.report is None else f"<h1>{_esc(page.plan.title)}</h1>"
        plan_html = itinerary_map.with_map(page.plan.html, list(page.plan.places))
        body += f'<section class="plan">{heading}{plan_html}</section>'
    elif page.report is not None:
        body += '<p class="muted">No plan written for this trip yet.</p>'
    return _document(
        f"{page.name} · Vacation deal watch", header, _tabs(pages, page.slug, "../"), body, footer,
        with_map=bool(page.plan and page.plan.places),
    )


def _ideas_page(suggestions: list[Suggestion], config: Config, pages: list[Page], footer: str) -> str:
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
                "<details><summary style='cursor:pointer;color:var(--accent);font-size:13px;"
                f"margin-top:6px'>Show config to paste</summary><pre>{_esc(snippet)}</pre></details>"
                if snippet
                else ""
            )
            + "</li>"
        )
    body = (
        '<section class="card"><p class="why">Matched on the tags of trips you already track. '
        "Paste a block into <code>config/trips.yaml</code> to start tracking it.</p>"
        + (f"<ul style='padding-left:18px;margin:12px 0 0'>{''.join(items)}</ul>" if items
           else "<p>No suggestions right now.</p>")
        + "</section>"
    )
    header = '<p class="eyebrow"><a href="index.html">Vacation deal watch</a></p><h1>Worth tracking too?</h1>'
    return _document("Ideas · Vacation deal watch", header, _tabs(pages, "ideas", ""), body, footer)


# --------------------------------------------------------------------------- #

def build(
    reports: list[TripReport],
    config: Config,
    suggestions: list[Suggestion] | None = None,
    output: Path = OUTPUT,
    spreadsheet_url: str = "",
    plans_dir: Path = PLANS_DIR,
) -> Path:
    now = datetime.now(timezone.utc)
    pages = _pages(reports, plans_dir)
    stamp = f"last checked {now:%b %d, %Y at %H:%M} UTC"
    sheet = (
        f' · <a href="{_esc(spreadsheet_url)}">every price as a spreadsheet</a>'
        if spreadsheet_url
        else ""
    )
    footer = (
        "Prices are the cheapest option found across the configured dates, airports and "
        "hotels, not a quote. Availability changes faster than this page. "
        f"{stamp}{sheet}."
    )

    tracked = [p for p in pages if p.report is not None]
    home_header = (
        '<p class="eyebrow">Two adults, four kids · from St. Louis</p>'
        "<h1>Vacation deal watch</h1>"
        f'<p class="sub">{len([p for p in tracked if p.report.has_data])} trip(s) priced · '
        f'{stamp}{sheet} · <a href="ideas.html">ideas</a></p>'
    )
    home = _document(
        "Vacation Deal Watch",
        home_header,
        _tabs(pages, "home", ""),
        # Cards are for trips; the resort list stays a tab.
        '<div class="cards">'
        + "".join(_card(p) for p in pages if p.report is not None or (p.plan and p.plan.nights))
        + "</div>",
        footer,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(home)

    trips_dir = output.parent / "trips"
    trips_dir.mkdir(exist_ok=True)
    written = set()
    for page in pages:
        target = trips_dir / f"{page.slug}.html"
        target.write_text(_trip_page(page, pages, stamp, footer))
        written.add(target.name)
    for stale in trips_dir.glob("*.html"):
        if stale.name not in written:
            stale.unlink()

    (output.parent / "ideas.html").write_text(
        _ideas_page(suggestions or [], config, pages, footer)
    )
    return output
