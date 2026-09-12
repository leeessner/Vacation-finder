"""Command line entry points, driven by the GitHub Actions workflows.

    python -m vacation_finder validate   # check config, estimate API usage
    python -m vacation_finder track      # price everything, record, alert
    python -m vacation_finder digest     # send the scheduled summary email
    python -m vacation_finder dashboard  # rebuild docs/index.html
    python -m vacation_finder demo       # fake history, to preview the output
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import alerts as alerts_mod
from . import dashboard as dashboard_mod
from . import email_report
from .config import Config, ConfigError, load_config
from .dates import candidate_windows
from .pricing import TripPricer
from .report import build_reports
from .storage import HISTORY_DIR, append_run, load_history, load_state
from .suggest import load_catalog, suggest

log = logging.getLogger("vacation_finder")

DEFAULT_CONFIG = "config/trips.yaml"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _load(path: str) -> Config:
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _dashboard_url() -> str:
    explicit = os.environ.get("DASHBOARD_URL", "")
    if explicit:
        return explicit
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return ""


# --------------------------------------------------------------------------- #

def money_fuel(trip) -> str:
    return f"${trip.ground.fuel_cost:,.0f} fuel"


def cmd_validate(args: argparse.Namespace) -> int:
    config = _load(args.config)
    today = date.today()
    monthly_calls = 0

    print(f"{len(config.trips)} trip(s) configured, {len(config.active_trips)} active\n")
    for trip in config.trips:
        windows = candidate_windows(trip, today, config.settings)
        dests, n_windows = len(trip.destinations), len(windows)

        # Only Amadeus calls count against the 2,000/month free quota. The
        # Google Flights scrape is free and unmetered, so it is reported
        # separately rather than inflating the quota estimate.
        flight_calls = dests * n_windows * len(trip.origins) if trip.flights.enabled else 0
        hotel_calls = (dests * n_windows + dests) if trip.lodging.enabled else 0
        per_run = flight_calls + hotel_calls
        scrapes = (
            dests * n_windows * len(trip.origins)
            if trip.flights.enabled and config.settings.google_flights_enabled
            else 0
        )
        if trip.active:
            monthly_calls += per_run * 30
        flag = "" if trip.active else "  (inactive)"
        print(f"  {trip.id}{flag}")
        print(f"    {trip.name} — {trip.party.adults} adult(s), {len(trip.party.children)} child(ren)")
        if trip.travel_mode == "drive":
            miles = trip.ground.drive_round_trip_miles
            print(
                f"    driving to {', '.join(d.display for d in trip.destinations)}"
                + (f" ({miles:,.0f} mi round trip, ~{money_fuel(trip)})" if miles else "")
            )
        else:
            print(
                f"    from {', '.join(trip.origins)} to "
                f"{', '.join(d.code for d in trip.destinations)}"
                + ("" if trip.flights.enabled else "  [airfare not priced yet]")
            )
        capacity = trip.lodging.rooms * 4
        if trip.lodging.enabled and trip.party.total > capacity:
            print(
                f"    WARNING: {trip.party.total} travellers in {trip.lodging.rooms} room(s). "
                f"Most hotels cap at 4 per room — set lodging.rooms higher or "
                f"prices will be unrealistically low."
            )
        if windows:
            print(
                f"    {len(windows)} date window(s) per run, "
                f"first {windows[0][0]} → {windows[0][1]}"
            )
        else:
            print("    WARNING: no valid departure dates — check dates.earliest/latest")
        print(
            f"    ~{per_run} Amadeus call(s) per run"
            + (f" + {scrapes} free Google scrape(s)" if scrapes else "")
        )
        print(f"    tags: {', '.join(trip.tags) or 'none'}")

    QUOTA = 2000
    pct = monthly_calls / QUOTA * 100
    print(
        f"\nEstimated ~{monthly_calls:,} Amadeus calls/month at one run per day "
        f"({pct:.0f}% of the {QUOTA:,} free quota)."
    )
    if monthly_calls > QUOTA:
        print("  OVER QUOTA. Lower settings.max_date_samples_per_trip, narrow a")
        print("  date window, or deactivate a trip.")
    elif pct > 80:
        print("  Little headroom left. Narrowing a date window is the cheapest fix:")
        print("  halving the samples on one trip halves its cost.")
    return 0


def cmd_track(args: argparse.Namespace) -> int:
    config = _load(args.config)
    pricer = TripPricer(config)
    trips_by_id = {t.id: t for t in config.trips}
    fired: list[alerts_mod.Alert] = []
    state = load_state(alerts_mod.STATE_NAME)

    for trip in config.active_trips:
        log.info("pricing %s", trip.id)
        result = pricer.price_trip(trip)
        for error in result.errors:
            log.warning("%s: %s", trip.id, error)

        row = append_run(result)
        if row is None:
            log.error("%s: no priceable option found this run", trip.id)
            continue
        log.info(
            "%s: best %s %.0f (%s → %s) from %d option(s), %d API call(s)",
            trip.id, row.currency, row.total, row.depart_date, row.return_date,
            len(result.quotes), result.queries_made,
        )

        from .stats import assess

        assessment = assess(load_history(trip.id), field="total")
        if assessment:
            candidates = alerts_mod.evaluate(trip, row, assessment)
            new_alerts, state = alerts_mod.filter_new(candidates, trips_by_id, state=state)
            fired.extend(new_alerts)

    alerts_mod.persist(state)

    reports = build_reports(config)
    suggestions = suggest(config, load_catalog())
    dashboard_mod.build(reports, config, suggestions)
    log.info("dashboard written to docs/index.html")

    if fired:
        subject, html, text = email_report.render_alerts_only(fired, _dashboard_url())
        _deliver(subject, html, text, args)
    else:
        log.info("no new alerts")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    config = _load(args.config)
    reports = build_reports(config)
    if not any(r.has_data for r in reports):
        log.warning("no price history yet — run 'track' at least once first")
    suggestions = suggest(config, load_catalog())
    subject, html, text = email_report.render_digest(
        reports, [], suggestions, config, _dashboard_url()
    )
    _deliver(subject, html, text, args)
    dashboard_mod.build(reports, config, suggestions)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    config = _load(args.config)
    path = dashboard_mod.build(build_reports(config), config, suggest(config, load_catalog()))
    print(f"wrote {path}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Synthesize plausible history so the email and dashboard can be reviewed."""
    config = _load(args.config)
    rng = random.Random(args.seed)
    today = date.today()

    for trip in config.active_trips:
        path = HISTORY_DIR / f"{trip.id}.csv"
        if _has_real_history(path) and not args.force:
            print(
                f"refusing to overwrite real price history in {path} "
                f"(pass --force if you really mean to)",
                file=sys.stderr,
            )
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        destination = trip.destinations[0]
        windows = candidate_windows(trip, today, config.settings)
        depart, ret = windows[0] if windows else (today + timedelta(days=90), today + timedelta(days=97))
        nights = (ret - depart).days

        base_flight = 320 * trip.party.total * rng.uniform(0.85, 1.2)
        base_lodging = 240 * nights * rng.uniform(0.8, 1.4)
        lines = ["captured_at,trip_id,destination,depart_date,return_date,nights,currency,"
                 "flight_total,flight_source,flight_carriers,flight_stops_out,flight_stops_back,"
                 "lodging_name,lodging_total,lodging_per_night,lodging_rating,lodging_source,"
                 "ground_total,total,is_complete,options_priced,notes"]

        flight, lodging = base_flight, base_lodging
        for days_ago in range(args.days, 0, -1):
            stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
            flight = max(180.0, flight * rng.uniform(0.96, 1.045))
            lodging = max(300.0, lodging * rng.uniform(0.97, 1.035))
            ground = trip.ground.cost_for(nights)
            total = flight + lodging + ground
            lines.append(
                f"{stamp.replace(microsecond=0).isoformat()},{trip.id},{destination.code},"
                f"{depart},{ret},{nights},{config.settings.currency},"
                f"{flight:.2f},demo,AA/DL,0,1,"
                f"Demo Beach Resort & Spa,{lodging:.2f},{lodging / nights:.2f},4,demo,"
                f"{ground:.2f},{total:.2f},true,6,"
            )
        path.write_text("\n".join(lines) + "\n")
        print(f"wrote {args.days} synthetic days to {path}")

    reports = build_reports(config)
    suggestions = suggest(config, load_catalog())
    dashboard_mod.build(reports, config, suggestions)
    subject, html, text = email_report.render_digest(
        reports, [], suggestions, config, _dashboard_url()
    )
    preview = Path(args.out)
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(html)
    print(f"dashboard: docs/index.html\nemail preview: {preview}\nsubject: {subject}")
    return 0


def _has_real_history(path: Path) -> bool:
    """True if the file holds observations that did not come from `demo`."""
    if not path.exists():
        return False
    rows = [r for r in load_history(path.stem) if r.flight_source != "demo"]
    return bool(rows)


def _deliver(subject: str, html: str, text: str, args: argparse.Namespace) -> None:
    if args.dry_run:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        print(f"[dry run] subject: {subject}\n[dry run] html written to {path}")
        return
    try:
        email_report.send_email(subject, html, text)
    except email_report.EmailNotConfigured as exc:
        log.warning("email not sent: %s", exc)
    except Exception as exc:  # noqa: BLE001 - a mail failure must not fail the run
        log.error("email delivery failed: %s", exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vacation_finder", description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="write email to a file, don't send")
    parser.add_argument("--out", default="build/email-preview.html")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check config and estimate API usage").set_defaults(func=cmd_validate)
    sub.add_parser("track", help="price trips, record history, fire alerts").set_defaults(func=cmd_track)
    sub.add_parser("digest", help="send the scheduled summary email").set_defaults(func=cmd_digest)
    sub.add_parser("dashboard", help="rebuild docs/index.html").set_defaults(func=cmd_dashboard)

    demo = sub.add_parser("demo", help="generate synthetic history to preview output")
    demo.add_argument("--days", type=int, default=60)
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--force", action="store_true", help="overwrite real history")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
