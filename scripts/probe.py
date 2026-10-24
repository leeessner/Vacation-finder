"""One-off live probe of SerpApi behaviour. Temporary — removed after use."""
import math
import sys

sys.path.insert(0, "src")
from vacation_finder.sources.google_hotels import SerpApiClient  # noqa: E402

client = SerpApiClient()
print("searches left:", client.searches_left())
BASE = {"currency": "USD", "gl": "us", "hl": "en", "rating": 8, "hotel_class": "3,4,5"}


def miles(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def run(label, center, params):
    print(f"\n===== {label} =====")
    try:
        payload = client.search({**BASE, **params})
    except Exception as exc:  # noqa: BLE001
        print("FAILED:", exc)
        return
    props = payload.get("properties") or []
    info = payload.get("search_information") or {}
    print("properties:", len(props), "| search_information:", info)
    print("pagination:", bool(payload.get("serpapi_pagination")))
    for p in props[:20]:
        gps = p.get("gps_coordinates") or {}
        d = miles(center, (gps.get("latitude"), gps.get("longitude"))) if gps else float("nan")
        print(
            f"  {d:5.1f} mi  {str(p.get('name'))[:44]:<44} class={p.get('extracted_hotel_class')} "
            f"rating={p.get('overall_rating')} total={((p.get('total_rate') or {}).get('extracted_lowest'))}"
        )


NASH = (36.1627, -86.7816)   # downtown Nashville
PARIS = (48.8566, 2.3522)    # Hôtel de Ville, central Paris
NASH_DATES = {"check_in_date": "2027-03-12", "check_out_date": "2027-03-16",
              "adults": 2, "children": 4, "children_ages": "8,7,5,3"}

run("N1: 'Downtown Nashville, TN', 1 room for 6, cheapest first", NASH,
    {**NASH_DATES, "q": "Downtown Nashville, TN", "sort_by": 3})
run("N2: 'Nashville, TN', 1 room for 6, Google's default order", NASH,
    {**NASH_DATES, "q": "Nashville, TN"})
run("P1: 'Paris, France', 1 adult + 2 kids per room, cheapest first", PARIS,
    {"q": "Paris, France", "check_in_date": "2027-06-05", "check_out_date": "2027-06-14",
     "adults": 1, "children": 2, "children_ages": "8,7", "sort_by": 3})
print("\nsearches left:", client.searches_left())
