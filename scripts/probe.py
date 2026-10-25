"""Temporary: run the real flight search for every summer trip, live."""
import sys
from datetime import date

sys.path.insert(0, "src")
from vacation_finder.config import load_config  # noqa: E402
from vacation_finder.dates import candidate_windows  # noqa: E402
from vacation_finder.insights import flying_trips  # noqa: E402
from vacation_finder.sources.google_flights import search_flights  # noqa: E402

cfg = load_config("config/trips.yaml")
for trip in flying_trips(cfg):
    for depart, ret in candidate_windows(trip, date.today(), cfg.settings):
        q = search_flights(trip, trip.origins[0], trip.destinations[0].code, depart, ret,
                           cfg.settings.currency, cfg.settings)
        result = f"{q.total_price:,.0f} USD via {q.source} ({'/'.join(q.carriers)})" if q else "NO FARE"
        print(f"{trip.id:24s} STL-{trip.destinations[0].code} {depart}..{ret}: {result}")
