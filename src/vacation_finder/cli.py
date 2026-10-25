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
from .summary import write_summary

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


def _spreadsheet_url() -> str:
    """Where data/prices-over-time.csv can be opened on GitHub."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "")
    if "/" in repo and branch:
        return f"https://github.com/{repo}/blob/{branch}/data/prices-over-time.csv"
    return ""


# --------------------------------------------------------------------------- #

def money_fuel(trip) -> str:
    return f"${trip.ground.fuel_cost:,.0f} fuel"


def cmd_validate(args: argparse.Namespace) -> int:
    from .lodging import lodging_slots

    config = _load(args.config)
    today = date.today()
    settings = config.settings

    print(f"{len(config.trips)} trip(s) configured, {len(config.active_trips)} active\n")
    for trip in config.trips:
        windows = candidate_windows(trip, today, settings)
        dests, n_windows = len(trip.destinations), len(windows)
        hotel_slots = dests * n_windows if trip.lodging.enabled else 0
        scrapes = (
            dests * n_windows * len(trip.origins)
            if trip.flights.enabled and settings.google_flights_enabled
            else 0
        )
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
        if trip.lodging.enabled:
            where = ", ".join(f'"{d.lodging_query}"' for d in trip.destinations)
            if trip.lodging.kind == "rental":
                bedrooms = trip.lodging.bedrooms or trip.lodging.rooms
                print(f"    lodging: one vacation rental, {bedrooms}+ bedroom(s), near {where}")
            else:
                rooms = trip.lodging.rooms
                what = (
                    f"one room for all {trip.party.total} (in practice a suite)"
                    if rooms == 1 and trip.party.total > 4
                    else f"{rooms} hotel room(s)"
                )
                print(f"    lodging: {what} near {where}")
        if trip.lodging.coverage == "none":
            print("    NOTE: lodging not tracked (coverage: none)")
        elif trip.lodging.coverage == "thin":
            print("    NOTE: thin lodging coverage — treat the price as a floor")
        if not trip.flights.enabled and not trip.lodging.enabled:
            print("    WARNING: nothing to track — neither flights nor lodging are priced")

        for dest in trip.destinations:
            hours = dest.drive_hours
            limit = settings.max_drive_hours
            if hours is not None and trip.travel_mode == "air" and hours <= limit:
                print(
                    f"    WARNING: {dest.display} is a {hours:g}-hour drive — within your "
                    f"{limit:g}-hour rule. Set `travel: drive` on this trip."
                )
            if trip.lodging.enabled and dest.center is None:
                print(
                    f"    NOTE: no `center` for {dest.display} — the cheapest hotel may be "
                    "far from anything you'd do"
                )
            elif trip.lodging.enabled and dest.within_miles:
                print(f"    hotels within {dest.within_miles:g} mi of the center")
        if trip.lodging.prefer:
            print(f"    preferred: {', '.join(trip.lodging.prefer)} (tracked when one qualifies)")
        if windows:
            print(
                f"    {len(windows)} date window(s) per run, "
                f"first {windows[0][0]} → {windows[0][1]}"
            )
        else:
            print("    WARNING: no valid departure dates — check dates.earliest/latest")
        parts = []
        if hotel_slots:
            parts.append(f"{hotel_slots} hotel slot(s)")
        if scrapes:
            parts.append(f"{scrapes} Google Flights scrape(s) per run")
        if parts:
            print(f"    {' + '.join(parts)}")
        print(f"    tags: {', '.join(trip.tags) or 'none'}")

    # SerpApi searches are the scarce resource: 250 free a month, shared by
    # hotel prices and the weekly Google Flights check for each flying trip.
    from .insights import flying_trips
    from .lodging import slot_cost

    slots = lodging_slots(config, today)
    cycle_cost = sum(slot_cost(trip) for trip, *_ in slots)
    per_run = settings.searches_per_run
    flights_per_run = len(flying_trips(config)) / max(1, settings.flight_insight_days)
    hotel_per_run = max(0.0, per_run - flights_per_run)
    print(f"\nSerpApi budget: up to {per_run} search(es) per daily run, of 250 free a month.")
    print(
        f"  Flights: {len(flying_trips(config))} trip(s) checked every "
        f"{settings.flight_insight_days} days (~{flights_per_run * 30:.0f}/month)."
    )
    if slots:
        cycle = cycle_cost / hotel_per_run if hotel_per_run else float("inf")
        print(
            f"  Hotels: {len(slots)} slot(s) costing {cycle_cost} search(es) per full pass; "
            f"each re-checked about every {cycle:.0f} day(s)."
        )
        if cycle > settings.hotel_price_max_age_days:
            print(
                "  WARNING: hotel prices will age past hotel_price_max_age_days before "
                "being re-checked — fewer date windows, or more searches per run."
            )
    monthly = per_run * 31
    if monthly + settings.hotel_search_reserve > 250:
        print(
            f"  WARNING: up to {monthly} searches in a 31-day month plus the reserve is more "
            "than the free 250; the tracker stops searching when the month runs out."
        )
    return 0


def cmd_track(args: argparse.Namespace) -> int:
    from . import insights as insights_mod
    from .lodging import LodgingBook, run_allowance
    from .sources.google_hotels import SIGNUP_HINT, SerpApiClient

    config = _load(args.config)
    hotels = SerpApiClient()
    settings = config.settings
    today = date.today()
    if hotels.configured:
        left = hotels.searches_left()
        allowance = run_allowance(left, settings.searches_per_run, settings.hotel_search_reserve)
        if left is not None:
            log.info("SerpApi: %d search(es) left this month", left)
        reason = "" if allowance else "this month's free SerpApi searches are used up"
    else:
        allowance, reason = 0, f"SERPAPI_KEY is not set — {SIGNUP_HINT}"

    # Flight insights first: one search a week per flying trip, and the only
    # source of "is this fare typical?" Hotels get what's left.
    insights = insights_mod.load_insights()
    for trip in insights_mod.due(config, insights, today)[:allowance]:
        fresh = insights_mod.fetch(hotels, trip, config, today)
        allowance -= 1
        if fresh is not None:
            insights[trip.id] = fresh
            log.info("%s: Google Flights insight — %s", trip.id, fresh.phrase())
    insights_mod.save_insights(insights)

    book = LodgingBook.load(config, today, allowance, unavailable_reason=reason)
    pricer = TripPricer(config, hotels=hotels, book=book, insights=insights)
    trips_by_id = {t.id: t for t in config.trips}
    fired: list[alerts_mod.Alert] = []
    state = load_state(alerts_mod.STATE_NAME)

    for trip in config.active_trips:
        log.info("pricing %s", trip.id)
        result = pricer.price_trip(trip, today=today)
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

        if not row.is_complete:
            continue    # an incomplete total is not a deal, however low it looks

        from .stats import assess

        assessment = assess(load_history(trip.id), field="total")
        if assessment:
            candidates = alerts_mod.evaluate(trip, row, assessment)
            new_alerts, state = alerts_mod.filter_new(candidates, trips_by_id, state=state)
            fired.extend(new_alerts)

    alerts_mod.persist(state)
    book.save()

    reports = build_reports(config)
    suggestions = suggest(config, load_catalog())
    dashboard_mod.build(reports, config, suggestions, spreadsheet_url=_spreadsheet_url())
    log.info("dashboard written to docs/index.html")
    write_summary(config)

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
    dashboard_mod.build(reports, config, suggestions, spreadsheet_url=_spreadsheet_url())
    write_summary(config)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    config = _load(args.config)
    path = dashboard_mod.build(
        build_reports(config), config, suggest(config, load_catalog()),
        spreadsheet_url=_spreadsheet_url(),
    )
    print(f"wrote {path}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Synthesize plausible history so the email and dashboard can be reviewed.

    This goes through the same storage path as a real run rather than writing
    CSV by hand, so the demo can never drift from the real schema.
    """
    from .models import FlightQuote, LodgingQuote, TripQuote
    from .pricing import RunResult

    from .insights import load_insights

    config = _load(args.config)
    rng = random.Random(args.seed)
    today = date.today()
    demo_insights = load_insights()

    for trip in config.active_trips:
        path = HISTORY_DIR / f"{trip.id}.csv"
        if _has_real_history(path) and not args.force:
            print(
                f"refusing to overwrite real price history in {path} "
                f"(pass --force if you really mean to)",
                file=sys.stderr,
            )
            return 1
        path.unlink(missing_ok=True)

        destination = trip.destinations[0]
        windows = candidate_windows(trip, today, config.settings)
        depart, ret = (
            windows[0]
            if windows
            else (today + timedelta(days=90), today + timedelta(days=97))
        )
        nights = (ret - depart).days

        # Start from Google's typical fare where the tracker has one, so the
        # preview's outlook isn't fooled by a made-up bargain.
        known = demo_insights.get(trip.id)
        flight = (
            known.lowest * rng.uniform(0.95, 1.1)
            if known and known.lowest
            else 340 * trip.party.total * rng.uniform(0.85, 1.2)
        )
        lodging = 210 * nights * trip.lodging.rooms * rng.uniform(0.8, 1.4)

        for days_ago in range(args.days, 0, -1):
            flight = max(180.0, flight * rng.uniform(0.96, 1.045))
            lodging = max(300.0, lodging * rng.uniform(0.97, 1.035))

            quote = TripQuote(
                trip_id=trip.id,
                destination=destination.code,
                depart_date=depart,
                return_date=ret,
                nights=nights,
                currency=config.settings.currency,
                flight=(
                    FlightQuote(
                        origin=trip.origins[0],
                        destination=destination.code,
                        depart_date=depart,
                        return_date=ret,
                        total_price=round(flight, 2),
                        currency=config.settings.currency,
                        carriers=("AA",),
                        stops_out=1,
                        stops_back=1,
                        source="demo",
                    )
                    if trip.flights.enabled
                    else None
                ),
                lodging=LodgingQuote(
                    destination=destination.code,
                    check_in=depart,
                    check_out=ret,
                    property_name="Demo Hotel & Suites",
                    total_price=round(lodging, 2),
                    currency=config.settings.currency,
                    nights=nights,
                    rating=4.0,
                    source="demo",
                ),
                ground_cost=trip.ground.cost_for(nights),
                ground_notes=trip.ground.notes,
                travel_mode=trip.travel_mode,
                flights_expected=trip.flights.enabled,
            )
            append_run(
                RunResult(
                    trip_id=trip.id,
                    captured_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
                    quotes=[quote],
                )
            )
        print(f"wrote {args.days} synthetic days to {path}")

    reports = build_reports(config)
    suggestions = suggest(config, load_catalog())
    dashboard_mod.build(reports, config, suggestions, spreadsheet_url=_spreadsheet_url())
    subject, html, text = email_report.render_digest(
        reports, [], suggestions, config, _dashboard_url()
    )
    preview = Path(args.out)
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(html)
    sheet = write_summary(config)
    print(
        f"dashboard: docs/index.html\nemail preview: {preview}\n"
        f"spreadsheet: {sheet}\nsubject: {subject}"
    )
    return 0


def _has_real_history(path: Path) -> bool:
    """True if the file holds observations that did not come from `demo`."""
    if not path.exists():
        return False
    rows = [r for r in load_history(path.stem) if r.flight_source != "demo"]
    return bool(rows)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Make one real call to each source and show exactly what came back.

    Everything else in this project was written without live access to these
    APIs, so this exists to make the first real contact a single command with
    readable output instead of a failed workflow run at 9am.
    """
    from .sources import google_flights, google_hotels
    from .sources.base import SourceError, SourceUnavailable
    from .sources.travelpayouts import TravelpayoutsClient

    config = _load(args.config)
    trip = next((t for t in config.active_trips if t.lodging.enabled), None)
    if trip is None:
        print("no active trip with lodging to test with", file=sys.stderr)
        return 1

    destination = trip.destinations[0]
    windows = candidate_windows(trip, date.today(), config.settings)
    if not windows:
        print(f"{trip.id} has no departure dates left to test with", file=sys.stderr)
        return 1
    depart, ret = windows[0]
    nights = (ret - depart).days or 1
    currency = config.settings.currency
    ok = True

    print(f"Testing with: {trip.name}  {depart} to {ret} ({nights} nights)\n")

    hotels = google_hotels.SerpApiClient()
    print("1. SerpApi key (hotel prices)")
    if not hotels.configured:
        print(f"   MISSING — add the SERPAPI_KEY secret; {google_hotels.SIGNUP_HINT}")
        return 1
    print(f"   present ({hotels.api_key[:4]}...{hotels.api_key[-4:]})")
    left = hotels.searches_left()
    print(
        f"   {left} search(es) left this month" if left is not None
        else "   could not read the account's remaining searches"
    )

    query = destination.lodging_query
    params = google_hotels.search_params(trip, query, depart, ret, currency)
    if trip.lodging.kind == "rental":
        what = f"one vacation rental, {params.get('bedrooms')}+ bedrooms, whole party"
    else:
        what = f"{trip.lodging.rooms} room(s), each priced for"
    guests = f"{params['adults']} adult(s)" + (
        f" + children aged {params['children_ages']}" if params.get("children_ages") else ""
    )
    print(f'\n2. Google Hotels: "{query}" — {what} {guests}  (uses 1 search)')
    try:
        payload = hotels.search(params)
    except (SourceError, SourceUnavailable) as exc:
        print(f"   FAILED: {exc}")
        payload, ok = None, False

    if payload is not None:
        props = [p for p in payload.get("properties") or [] if isinstance(p, dict)]
        center = destination.center
        print(f"   {len(props)} propert(ies) returned, cheapest first:")
        for prop in props[:8]:
            stay = google_hotels.stay_price(prop, nights)
            stars = prop.get("extracted_hotel_class")
            dist = (
                google_hotels.distance_from(center, prop.get("gps_coordinates"))
                if center else None
            )
            print(
                f"     {str(prop.get('name'))[:38]:<38} "
                f"{(str(stars) + '*') if stars else '  ':>3} "
                f"{prop.get('overall_rating') or '-':>4} "
                + (f"{dist:5.1f} mi  " if dist is not None else "         ")
                + (f"{stay:>8,.0f} {currency} stay / {stay / nights:,.0f} a night"
                   if stay else "no price")
            )

        class _Replay:
            configured = True

            def search(self, _params, engine="google_hotels"):
                return payload

        pick = google_hotels.search_lodging(
            _Replay(), trip, query, depart, ret, currency, config.settings,
            center=center, within_miles=destination.within_miles,
        )
        rules = f"rating {trip.lodging.min_rating or '-'}+, " + (
            f"within {destination.within_miles:g} mi" if destination.within_miles else "any distance"
        )
        if pick:
            print(
                f"\n   -> cheapest meeting the rules ({rules}): {pick.property_name}, "
                f"{pick.total_price:,.0f} {currency} for the stay "
                f"({pick.per_night:,.0f} a night)"
                + (f", {pick.distance_miles:g} mi out" if pick.distance_miles is not None else "")
            )
            print(f"      check it: {pick.deep_link}")
        else:
            print(
                f"\n   -> nothing meets this trip's rules ({rules}, min_stars, "
                "max_per_night). Loosen them, or check the search phrase and center."
            )
            ok = False

        for brand in trip.lodging.prefer:
            place = destination.label or query
            print(f'\n   Preferred brand: "{brand} {place}"  (uses 1 search)')
            try:
                found = google_hotels.search_lodging(
                    hotels, trip, f"{brand} {place}", depart, ret, currency, config.settings,
                    center=center, within_miles=destination.within_miles, name_contains=brand,
                )
            except (SourceError, SourceUnavailable) as exc:
                print(f"   FAILED: {exc}")
                continue
            if found:
                print(
                    f"   -> {found.property_name}: {found.total_price:,.0f} {currency} "
                    f"({found.per_night:,.0f} a night)"
                    + (f", {found.distance_miles:g} mi out" if found.distance_miles is not None else "")
                    + " — this is what the tracker records"
                )
            else:
                print(f"   -> no {brand} meets the rules for these dates; the cheapest above is used")

    from . import insights as insights_mod

    flyer = next(iter(insights_mod.flying_trips(config)), None)
    print("\n3. Google Flights verdict (weekly, uses 1 search)")
    if flyer is None:
        print("   skipped — no trip has flights switched on")
    else:
        insight = insights_mod.fetch(hotels, flyer, config, date.today())
        if insight and (insight.lowest or insight.level):
            print(
                f"   {flyer.name} {insight.depart} to {insight.return_}: "
                f"lowest {insight.lowest or 0:,.0f} {currency} for the party"
                + (f" — {insight.phrase()}" if insight.phrase() else "")
            )
        else:
            print("   no verdict returned for this route and dates")

    print("\n4. Travelpayouts flights (optional)")
    client = TravelpayoutsClient()
    if not client.configured:
        print("   skipped — no TRAVELPAYOUTS_TOKEN; airfare comes from Google Flights")
    else:
        try:
            payload = client.get_flights(
                "/v2/prices/week-matrix",
                {
                    "origin": trip.origins[0] if trip.origins else "",
                    "destination": destination.code,
                    "depart_date": depart.isoformat(),
                    "return_date": ret.isoformat(),
                    "currency": currency.lower(),
                },
            )
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            print(f"   {len(rows)} cached fare(s) returned")
        except (SourceError, SourceUnavailable) as exc:
            print(f"   FAILED: {exc}")

    print("\n5. Google Flights scraper (no key needed)")
    scrape_trip = flyer or trip
    scrape_windows = candidate_windows(scrape_trip, date.today(), config.settings)
    if not google_flights.available():
        print(f"   unavailable: {google_flights.import_error()}")
    elif not scrape_trip.origins or not scrape_windows:
        print("   skipped — no origin airport or dates to test with")
    else:
        s_depart, s_ret = scrape_windows[0]
        print(f"   {scrape_trip.name} {s_depart} to {s_ret}")
        quote = google_flights.search_flights(
            scrape_trip, scrape_trip.origins[0], scrape_trip.destinations[0].code,
            s_depart, s_ret, currency, config.settings,
        )
        if quote:
            how = {
                "google_flights": "",
                "google_flights_scaled": " (estimated: smaller party's fare, scaled)",
                "google_flights_extra_stop": " (one more stop than your limit)",
                "google_flights_scaled_extra_stop": " (estimated, one more stop than your limit)",
            }.get(quote.source, f" ({quote.source})")
            print(f"   {quote.total_price:,.0f} {quote.currency} for the party{how}")
        else:
            print("   no result — the scraper may have broken, or the route has no fares")

    print("\n" + ("Hotel prices are working." if ok else "Something failed — see above."))
    return 0 if ok else 1


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

    # The same options, accepted after the subcommand too (`track --verbose`).
    # SUPPRESS keeps a subcommand from resetting a value given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--out", default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, **kwargs) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], **kwargs)

    add("validate", help="check config and estimate API usage").set_defaults(func=cmd_validate)
    add("track", help="price trips, record history, fire alerts").set_defaults(func=cmd_track)
    add("digest", help="send the scheduled summary email").set_defaults(func=cmd_digest)
    add("dashboard", help="rebuild docs/index.html").set_defaults(func=cmd_dashboard)
    add(
        "doctor", help="make one live call to each source and show what came back"
    ).set_defaults(func=cmd_doctor)

    demo = add("demo", help="generate synthetic history to preview output")
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
