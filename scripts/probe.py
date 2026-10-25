"""Temporary: diagnose why Google Flights scraping fails on international routes."""
import json
import sys
import traceback
from dataclasses import replace
from datetime import date

sys.path.insert(0, "src")
from fast_flights.fetcher import fetch_flights_html  # noqa: E402
from fast_flights.parser import parse  # noqa: E402
from selectolax.lexbor import LexborHTMLParser  # noqa: E402

from vacation_finder.config import FlightPrefs, load_config  # noqa: E402
from vacation_finder.models import Party  # noqa: E402
from vacation_finder.sources.google_flights import build_query  # noqa: E402

cfg = load_config("config/trips.yaml")
spain = cfg.trip("spain-barcelona-madrid")
JUNE = (date(2027, 6, 5), date(2027, 6, 15))
MARCH = (date(2027, 3, 12), date(2027, 3, 16))

cases = [
    ("A STL-BCN exactly as tracked", spain, "BCN", JUNE),
    ("B STL-BCN no checked bags", replace(spain, flights=replace(spain.flights, checked_bags=0)), "BCN", JUNE),
    ("C STL-BCN 1 adult, no bags", replace(spain, party=Party(adults=1), flights=replace(spain.flights, checked_bags=0)), "BCN", JUNE),
    ("D STL-BCN any stops, no bags", replace(spain, flights=FlightPrefs(max_stops=None)), "BCN", JUNE),
    ("E STL-CDG exactly as tracked", spain, "CDG", JUNE),
    ("F STL-BNA control (worked before)", replace(spain, flights=replace(spain.flights, checked_bags=0)), "BNA", MARCH),
]


def shape(x, depth=0):
    if isinstance(x, list):
        return f"list[{len(x)}]"
    return type(x).__name__


for label, trip, dest, (d, r) in cases:
    print(f"\n===== {label} =====")
    try:
        q = build_query(trip, "STL", dest, d, r, "USD")
        html = fetch_flights_html(q)
    except Exception as exc:  # noqa: BLE001
        print("FETCH FAILED:", repr(exc))
        continue
    print("html length:", len(html))
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        print("NO ds:1 SCRIPT. <title>:", (LexborHTMLParser(html).css_first("title") or None) and LexborHTMLParser(html).css_first("title").text())
        print("snippet:", html[:300].replace("\n", " "))
        continue
    try:
        res = parse(html)
        print("library parse OK:", len(res), "results; prices:", [f.price for f in res[:6]])
    except Exception:
        tb = traceback.format_exc().strip().splitlines()
        print("LIBRARY PARSE FAILED:", " | ".join(l.strip() for l in tb[-4:]))
    js = script.text()
    try:
        data = js.split("data:", 1)[1].rsplit(",", 1)[0]
        payload = json.loads(data)
    except Exception as exc:  # noqa: BLE001
        print("payload decode failed:", repr(exc), js[:200])
        continue
    print("payload:", len(payload), "top shapes:", [shape(p) for p in payload[:10]])
    for idx in (2, 3):
        block = payload[idx] if len(payload) > idx else None
        items = block[0] if isinstance(block, list) and block and isinstance(block[0], list) else None
        print(f"  payload[{idx}]: {shape(block)}; items: {shape(items)}")
        for n, k in enumerate((items or [])[:25]):
            flight = k[0] if isinstance(k, list) and k else None
            price_block = k[1] if isinstance(k, list) and len(k) > 1 else "MISSING"
            price = None
            try:
                price = price_block[0][1]
            except Exception:  # noqa: BLE001
                pass
            extras = flight[22] if isinstance(flight, list) and len(flight) > 22 else "MISSING"
            legs = flight[2] if isinstance(flight, list) and len(flight) > 2 else None
            bad_legs = []
            for leg in legs or []:
                for i in (3, 4, 5, 6, 8, 10, 11, 17, 20, 21):
                    if not isinstance(leg, list) or len(leg) <= i:
                        bad_legs.append(f"short:{i}")
                        break
                    if leg[i] is None and i in (20, 21):
                        bad_legs.append(f"leg[{i}]=None")
            problems = []
            if price is None:
                problems.append(f"price_block={str(price_block)[:60]}")
            if extras is None or extras == "MISSING":
                problems.append(f"flight[22]={extras}")
            if bad_legs:
                problems.append(f"legs:{bad_legs}")
            airlines = flight[1] if isinstance(flight, list) and len(flight) > 1 else None
            print(f"    #{n:2d} price={price} airlines={str(airlines)[:40]} {'PROBLEM ' + '; '.join(problems) if problems else ''}")
