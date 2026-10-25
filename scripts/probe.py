"""Temporary: why do Nice, Munich and one Barcelona date still return nothing?"""
import sys
from datetime import date

sys.path.insert(0, "src")
from fast_flights import FlightQuery, Passengers, create_query, get_flights  # noqa: E402


def run(label, dest, d, r, adults, children, stops):
    legs = [FlightQuery(date=d, from_airport="STL", to_airport=dest, max_stops=stops),
            FlightQuery(date=r, from_airport=dest, to_airport="STL", max_stops=stops)]
    q = create_query(flights=legs, trip="round-trip", seat="economy",
                     passengers=Passengers(adults=adults, children=children),
                     currency="USD", max_stops=stops)
    try:
        res = get_flights(q)
        best = min(res, key=lambda f: f.price) if res else None
        print(f"{label:42s} -> {len(res)} result(s)" + (f", cheapest {best.price} {best.airlines}" if best else ""))
    except Exception as exc:  # noqa: BLE001
        print(f"{label:42s} -> none ({type(exc).__name__})")


for dest, d, r in [("NCE", "2027-06-05", "2027-06-14"), ("MUC", "2027-06-05", "2027-06-15"),
                   ("BCN", "2027-07-07", "2027-07-17")]:
    print(f"\n== STL-{dest} {d} ==")
    run(f"{dest} 1 adult <=1 stop", dest, d, r, 1, 0, 1)
    run(f"{dest} 2 adults <=1 stop", dest, d, r, 2, 0, 1)
    run(f"{dest} 2 adults <=2 stops", dest, d, r, 2, 0, 2)
    run(f"{dest} 2 adults any stops", dest, d, r, 2, 0, None)
    run(f"{dest} family any stops", dest, d, r, 2, 4, None)
    run(f"{dest} 2 adults nonstop", dest, d, r, 2, 0, 0)
