"""Rendering and sending the digest and alert emails.

Email HTML is not web HTML: Gmail drops <style> blocks, flexbox, grid and
inline SVG. So everything here is tables with inline styles, and the price
chart is built from table cells with background colours rather than an image
or an SVG. It renders the same in Gmail, Apple Mail and Outlook.
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

from .alerts import Alert
from .config import Config
from .report import TripReport, delta_phrase, money
from .suggest import Suggestion, as_trip_yaml

log = logging.getLogger(__name__)

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK = "#111827"
MUTED = "#6b7280"
LINE = "#e5e7eb"
CHART_BARS = 30


class EmailNotConfigured(RuntimeError):
    """SMTP credentials are missing; the caller should log and carry on."""


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def bar_chart(series: list[tuple[date, float]], color: str, width: int = 520) -> str:
    """Column chart made of table cells — the only chart Gmail reliably renders."""
    points = series[-CHART_BARS:]
    if len(points) < 2:
        return (
            f'<p style="margin:8px 0 0;color:{MUTED};font-size:13px">'
            f"Not enough history to chart yet — this fills in as the tracker runs.</p>"
        )

    values = [v for _, v in points]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    height = 64
    bar_w = max(4, int(width / len(points)) - 2)

    cells = []
    for index, (_, value) in enumerate(points):
        filled = int(((value - low) / span) * (height - 8)) + 8
        is_low = value == low
        is_last = index == len(points) - 1
        fill = "#15803d" if is_low else (color if is_last else "#cbd5e1")
        cells.append(
            f'<td valign="bottom" style="padding:0 1px">'
            f'<div style="width:{bar_w}px;height:{filled}px;background:{fill};'
            f'border-radius:2px 2px 0 0;font-size:0;line-height:0">&nbsp;</div></td>'
        )

    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:12px 0 4px"><tr>{"".join(cells)}</tr></table>'
        f'<div style="font-size:11px;color:{MUTED}">'
        f"{points[0][0]:%b %d} → {points[-1][0]:%b %d} · "
        f"low {money(low)} · high {money(high)} · green = cheapest day</div>"
    )


def _row(label: str, value: str, bold: bool = False, color: str = INK) -> str:
    weight = "600" if bold else "400"
    return (
        f'<tr><td style="padding:4px 0;color:{MUTED};font-size:14px">{_esc(label)}</td>'
        f'<td align="right" style="padding:4px 0;color:{color};font-size:14px;'
        f'font-weight:{weight}">{value}</td></tr>'
    )


def trip_card(report: TripReport) -> str:
    trip, latest, sym = report.trip, report.latest, report.symbol

    if latest is None:
        return (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'border="0" style="border:1px solid {LINE};border-radius:10px;'
            f'padding:16px;margin:0 0 16px"><tr><td>'
            f'<div style="font-size:17px;font-weight:700;color:{INK}">{_esc(trip.name)}</div>'
            f'<div style="color:{MUTED};font-size:14px;margin-top:6px">'
            f"No prices captured yet. If this persists, check the workflow logs.</div>"
            f"</td></tr></table>"
        )

    assessment = report.total
    verdict_text = assessment.verdict if assessment else "—"
    rows = [
        _row("Flights (whole party)", money(latest.flight_total, sym)),
        _row(
            f"Lodging · {latest.nights} nights"
            + (f" · {latest.lodging_name[:34]}" if latest.lodging_name else ""),
            money(latest.lodging_total, sym),
        ),
    ]
    if latest.ground_total:
        rows.append(_row(trip.ground.notes or "Ground transport", money(latest.ground_total, sym)))
    rows.append(_row("Total", money(latest.total, sym), bold=True, color=report.status_color))

    extras = []
    if report.cheapest_ever and report.cheapest_ever.total < latest.total:
        extras.append(
            f"Best we've seen: {money(report.cheapest_ever.total, sym)} "
            f"on {report.cheapest_ever.captured_on:%b %d}"
        )
    vs_target = report.vs_target
    if vs_target is not None:
        word = "under" if vs_target < 0 else "over"
        extras.append(f"{money(abs(vs_target), sym)} {word} your {money(trip.target_total, sym)} target")
    delta = delta_phrase(assessment, sym)
    if delta:
        extras.append(delta)

    dates = f"{latest.depart_date:%b %d} – {latest.return_date:%b %d, %Y}"
    carriers = f" · {latest.flight_carriers}" if latest.flight_carriers else ""

    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border:1px solid {LINE};border-radius:10px;padding:16px;margin:0 0 16px">'
        f"<tr><td>"
        f'<div style="font-size:17px;font-weight:700;color:{INK}">{_esc(trip.name)}</div>'
        f'<div style="color:{MUTED};font-size:13px;margin-top:2px">'
        f"{_esc(latest.destination)} · {dates}{_esc(carriers)}</div>"
        f'<div style="margin-top:10px;display:inline-block;padding:3px 10px;border-radius:99px;'
        f'background:{report.status_color};color:#fff;font-size:12px;font-weight:600">'
        f"{_esc(verdict_text)}</div>"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin-top:12px;border-top:1px solid {LINE}">{"".join(rows)}</table>'
        + bar_chart(report.series, report.status_color)
        + (
            f'<div style="color:{MUTED};font-size:13px;margin-top:8px">'
            + " · ".join(_esc(e) for e in extras)
            + "</div>"
            if extras
            else ""
        )
        + "</td></tr></table>"
    )


def alert_block(alerts: list[Alert], sym: str = "$") -> str:
    if not alerts:
        return ""
    items = "".join(
        f'<tr><td style="padding:10px 14px;border-left:3px solid #15803d;background:#f0fdf4">'
        f'<div style="font-weight:700;color:#14532d;font-size:15px">{_esc(a.headline)}</div>'
        f'<div style="color:#166534;font-size:13px;margin-top:3px">{_esc(a.detail)}</div>'
        f"</td></tr>"
        f'<tr><td style="height:8px;font-size:0">&nbsp;</td></tr>'
        for a in alerts
    )
    return (
        f'<div style="font-size:13px;font-weight:700;color:{MUTED};'
        f'text-transform:uppercase;letter-spacing:.05em;margin:0 0 10px">Deal alerts</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="margin-bottom:12px">{items}</table>'
    )


def suggestion_block(suggestions: list[Suggestion], config: Config) -> str:
    """Short pitches only. The full YAML lives on the dashboard, not in your inbox."""
    if not suggestions:
        return ""
    cards = "".join(
        f'<tr><td style="padding:11px 0;border-bottom:1px solid {LINE}">'
        f'<div style="font-weight:600;color:{INK};font-size:15px">'
        f"{_esc(s.entry.label)}</div>"
        f'<div style="color:{MUTED};font-size:13px;margin-top:3px">{_esc(s.summary)}</div>'
        f"</td></tr>"
        for s in suggestions
    )
    names = ", ".join(s.entry.label.split(",")[0] for s in suggestions)
    return (
        f'<div style="font-size:13px;font-weight:700;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:.05em;margin:26px 0 4px">Worth tracking too?</div>'
        f'<div style="color:{MUTED};font-size:13px;margin-bottom:2px">'
        f"Matched against the tags on the trips you already track.</div>"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0">{cards}</table>'
        f'<div style="color:{MUTED};font-size:13px;margin-top:10px">'
        f"Want any of these tracked? Reply with the name "
        f"(for example &ldquo;track {_esc(names.split(', ')[0])}&rdquo;) and it gets added "
        f"to the next run — ready-to-paste config for each is on the dashboard.</div>"
    )


def _shell(title: str, body: str, footer: str) -> str:
    return (
        f'<div style="background:#f3f4f6;padding:24px 12px;font-family:{FONT}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:620px;margin:0 auto;background:#ffffff;border-radius:12px;'
        f'padding:24px"><tr><td>'
        f'<div style="font-size:22px;font-weight:800;color:{INK};margin-bottom:2px">{title}</div>'
        f"{body}"
        f'<div style="margin-top:22px;padding-top:14px;border-top:1px solid {LINE};'
        f'color:{MUTED};font-size:12px">{footer}</div>'
        f"</td></tr></table></div>"
    )


def render_digest(
    reports: list[TripReport],
    alerts: list[Alert],
    suggestions: list[Suggestion],
    config: Config,
    dashboard_url: str = "",
) -> tuple[str, str, str]:
    """Returns (subject, html, plain_text)."""
    today = datetime.now(timezone.utc).date()
    priced = [r for r in reports if r.has_data]
    movers = [r for r in priced if r.total and r.total.verdict in {"all-time low", "excellent", "good"}]

    if alerts:
        subject = f"🔻 {alerts[0].headline}" + (
            f" (+{len(alerts) - 1} more)" if len(alerts) > 1 else ""
        )
    elif movers:
        subject = f"Vacation watch — {len(movers)} of {len(priced)} trips are priced well"
    else:
        subject = f"Vacation watch — {today:%b %d}"

    body = (
        f'<div style="color:{MUTED};font-size:14px;margin-bottom:18px">'
        f"{today:%A, %B %d, %Y} · tracking {len(priced)} trip(s)</div>"
        + alert_block(alerts)
        + "".join(trip_card(r) for r in reports)
        + suggestion_block(suggestions, config)
    )
    footer = (
        (f'Full history and charts: <a href="{dashboard_url}">{dashboard_url}</a><br>' if dashboard_url else "")
        + "Prices are the cheapest option found across your configured dates and airports. "
        "Verify before booking — availability moves faster than any tracker."
    )
    return subject, _shell("Vacation deal watch", body, footer), _plain_digest(reports, alerts, today)


def _plain_digest(reports: list[TripReport], alerts: list[Alert], today: date) -> str:
    lines = [f"Vacation deal watch — {today:%B %d, %Y}", ""]
    if alerts:
        lines.append("DEAL ALERTS")
        lines += [f"  * {a.headline} — {a.detail}" for a in alerts]
        lines.append("")
    for report in reports:
        latest = report.latest
        if latest is None:
            lines.append(f"{report.trip.name}: no data yet")
            continue
        sym = report.symbol
        verdict = report.total.verdict if report.total else "—"
        lines.append(
            f"{report.trip.name} [{verdict}] {money(latest.total, sym)} total "
            f"({latest.depart_date:%b %d}–{latest.return_date:%b %d}) "
            f"— flights {money(latest.flight_total, sym)}, "
            f"lodging {money(latest.lodging_total, sym)}"
        )
    return "\n".join(lines)


def render_alerts_only(alerts: list[Alert], dashboard_url: str = "") -> tuple[str, str, str]:
    subject = f"🔻 {alerts[0].headline}" + (
        f" (+{len(alerts) - 1} more)" if len(alerts) > 1 else ""
    )
    body = (
        f'<div style="color:{MUTED};font-size:14px;margin-bottom:18px">'
        f"A price you're watching crossed a threshold you set.</div>" + alert_block(alerts)
    )
    footer = (
        f'Full history: <a href="{dashboard_url}">{dashboard_url}</a><br>' if dashboard_url else ""
    ) + "Verify availability before booking."
    text = "\n".join(f"* {a.headline}\n  {a.detail}" for a in alerts)
    return subject, _shell("Deal alert", body, footer), text


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #

def send_email(subject: str, html: str, text: str, to: str | None = None) -> None:
    """Send via SMTP. With Gmail, SMTP_PASSWORD must be an app password."""
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    recipient = to or os.environ.get("EMAIL_TO", user)
    sender = os.environ.get("EMAIL_FROM", user)

    if not (user and password and recipient):
        raise EmailNotConfigured(
            "set SMTP_USER, SMTP_PASSWORD and EMAIL_TO to enable email delivery"
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("Vacation Watch", sender))
    message["To"] = recipient
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)
    log.info("sent '%s' to %s", subject, recipient)
