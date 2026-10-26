"""Telling you what changed: a short update GitHub emails to you.

Each digest or deal alert is opened as an issue in this repository, assigned
to the repository owner. GitHub emails you about issues assigned to you, so
the update arrives in your inbox with no mail account, password or API key
anywhere: the workflow uses GitHub's own per-run token, which expires when
the run ends. The previous digest is closed when a new one opens, so the
Issues tab doesn't pile up.

The update is deliberately plain — what each trip costs today, which way it's
moving, and what to do — with links to the dashboard (charts) and the
spreadsheet (every price). Everything in it is already on the public
dashboard.
"""

from __future__ import annotations

import logging
import os
from datetime import date

import requests

from .alerts import Alert
from .report import (
    TripReport,
    display_notes,
    missing_parts,
    money,
    total_label,
    trend_lines,
    verdict_label,
)

log = logging.getLogger(__name__)

API = "https://api.github.com"
DIGEST_LABEL = "vacation-digest"
ALERT_LABEL = "vacation-alert"


class NotConfigured(RuntimeError):
    """No repository or token: running outside GitHub Actions."""


# --------------------------------------------------------------------------- #
# What it says
# --------------------------------------------------------------------------- #

def _links(dashboard_url: str, spreadsheet_url: str) -> str:
    links = []
    if dashboard_url:
        links.append(f"[Dashboard (charts)]({dashboard_url})")
    if spreadsheet_url:
        links.append(f"[Spreadsheet (every price)]({spreadsheet_url})")
    return " · ".join(links)


def _trip_section(report: TripReport) -> str:
    trip, latest, sym = report.trip, report.latest, report.symbol
    if latest is None:
        return f"### {trip.name}\nNo prices yet.\n"

    lines = [
        f"### {trip.name} — {money(latest.total, sym)}",
        f"{latest.depart_date:%b %d} – {latest.return_date:%b %d, %Y} · "
        f"*{verdict_label(report)}*"
        + ("" if total_label(report) == "Total" else f" · {total_label(report)}"),
    ]
    if report.outlook is not None:
        lines.append(
            f"**{report.outlook.label}** ({report.outlook.confidence} confidence): "
            + "; ".join(report.outlook.reasons)
        )
    moves = trend_lines(report)
    if moves:
        lines += [f"- {m}" for m in moves]
    if "hotel" in missing_parts(report):
        lines.append("- Hotel: being checked — within a few days")
    lines += [f"- {n}" for n in display_notes(latest)]
    return "\n".join(lines) + "\n"


def digest_markdown(
    reports: list[TripReport],
    today: date,
    dashboard_url: str = "",
    spreadsheet_url: str = "",
) -> tuple[str, str]:
    """(title, body) for the scheduled update."""
    priced = [r for r in reports if r.has_data]
    act = [
        r for r in priced
        if r.outlook and r.outlook.label in {"Good time to book", "Book soon"}
    ]
    title = f"Vacation watch — {today:%b %d}" + (
        f": {len(act)} to act on" if act else ""
    )
    head = [
        f"**{today:%A, %B %d}** · {len(priced)} trip(s) tracked",
        _links(dashboard_url, spreadsheet_url),
    ]
    if act:
        head.append(
            "**Worth acting on:** "
            + "; ".join(f"{r.trip.name} — {r.outlook.label.lower()}" for r in act)
        )
    body = "\n\n".join(h for h in head if h) + "\n\n" + "\n".join(
        _trip_section(r) for r in reports
    )
    body += (
        "\n---\nPrices are the cheapest option found under your rules, not a quote. "
        "Verify before booking."
    )
    return title, body


def alerts_markdown(
    alerts: list[Alert], dashboard_url: str = "", spreadsheet_url: str = ""
) -> tuple[str, str]:
    """(title, body) for a deal alert."""
    # Same "Vacation watch" prefix as the digest, so one mail filter can
    # forward both and nothing else from GitHub.
    title = f"🔻 Vacation watch: {alerts[0].headline}" + (
        f" (+{len(alerts) - 1} more)" if len(alerts) > 1 else ""
    )
    body = "\n\n".join(
        [f"**{a.headline}**\n\n{a.detail}" for a in alerts]
        + [_links(dashboard_url, spreadsheet_url)]
    )
    return title, body


# --------------------------------------------------------------------------- #
# Sending it
# --------------------------------------------------------------------------- #

class GitHubNotifier:
    """Opens the update as an issue assigned to the person to notify."""

    def __init__(
        self,
        repo: str | None = None,
        token: str | None = None,
        notify_user: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.repo = repo if repo is not None else os.environ.get("GITHUB_REPOSITORY", "")
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "") or self.repo.split("/")[0]
        self.user = notify_user or os.environ.get("NOTIFY_USER", "") or owner
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.repo and self.token and self.user)

    def _call(self, method: str, path: str, **kwargs) -> requests.Response:
        return self.session.request(
            method,
            f"{API}/repos/{self.repo}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            **kwargs,
        )

    def post(self, title: str, body: str, label: str, close_previous: bool = False) -> int:
        """Open the issue; returns its number. With `close_previous`, earlier
        open issues with the same label are closed first."""
        if not self.configured:
            raise NotConfigured("GITHUB_REPOSITORY and GITHUB_TOKEN are only set in Actions")
        if close_previous:
            self._close_open(label)
        payload = {
            "title": title,
            # The @-mention is a second route to a notification, in case
            # assignment emails are switched off.
            "body": f"@{self.user}\n\n{body}",
            "assignees": [self.user],
            "labels": [label],
        }
        response = self._call("POST", "/issues", json=payload)
        if response.status_code == 422:
            # An assignee who can't be assigned (e.g. NOTIFY_USER without
            # access) shouldn't lose the update; the @-mention still notifies.
            payload.pop("assignees")
            response = self._call("POST", "/issues", json=payload)
        if response.status_code >= 300:
            raise RuntimeError(f"GitHub {response.status_code}: {response.text[:200]}")
        number = int(response.json()["number"])
        log.info("opened issue #%d for @%s: %s", number, self.user, title)
        return number

    def _close_open(self, label: str) -> None:
        response = self._call("GET", "/issues", params={"state": "open", "labels": label})
        if response.status_code >= 300:
            log.info("could not list earlier %s issues: %s", label, response.status_code)
            return
        for issue in response.json() or []:
            if "pull_request" in issue:
                continue
            self._call(
                "PATCH", f"/issues/{issue['number']}",
                json={"state": "closed", "state_reason": "completed"},
            )
