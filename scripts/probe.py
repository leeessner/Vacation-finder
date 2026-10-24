"""One-off live probe of SerpApi behaviour. Temporary — removed after use."""
import json
import os
import sys

sys.path.insert(0, "src")
from vacation_finder.sources.google_hotels import SerpApiClient  # noqa: E402

client = SerpApiClient()
print("searches left:", client.searches_left())

BASE = {"currency": "USD", "gl": "us", "hl": "en"}
HOTEL = {**BASE, "check_in_date": "2027-03-12", "check_out_date": "2027-03-16"}


def hotels(label, params):
    print(f"\n===== {label} =====")
    try:
        payload = client.search({**HOTEL, **params})
    except Exception as exc:  # noqa: BLE001
        print("FAILED:", exc)
        return
    print("top-level keys:", sorted(payload.keys()))
    props = payload.get("properties") or []
    print("properties:", len(props))
    if props:
        print("first property keys:", sorted(props[0].keys()))
    for p in props[:12]:
        print(
            f"  {str(p.get('name'))[:46]:<46} type={p.get('type')!s:<16} "
            f"class={p.get('extracted_hotel_class')} rating={p.get('overall_rating')} "
            f"total={((p.get('total_rate') or {}).get('extracted_lowest'))} "
            f"night={((p.get('rate_per_night') or {}).get('extracted_lowest'))}"
        )
    if props:
        print("first property sample:", json.dumps(
            {k: props[0].get(k) for k in ("essential_info", "amenities", "nearby_places", "prices", "deal", "description") if k in props[0]},
        )[:1500])


hotels("A: Nashville, ONE room, 2 adults + 4 kids, rating 4.0+",
       {"q": "Nashville, TN", "adults": 2, "children": 4, "children_ages": "8,7,5,3",
        "rating": 8, "sort_by": 3})
hotels("B: 'Embassy Suites Nashville, TN', one room for six",
       {"q": "Embassy Suites Nashville, TN", "adults": 2, "children": 4,
        "children_ages": "8,7,5,3"})
hotels("C: Gatlinburg cabins (vacation rentals), 3+ bedrooms",
       {"q": "Gatlinburg, TN", "adults": 2, "children": 4, "children_ages": "8,7,5,3",
        "vacation_rentals": "true", "bedrooms": 3, "rating": 8, "sort_by": 3})

print("\n===== D: Google Flights STL->CDG Jun 5-14 2027, 2 adults + 4 kids =====")
try:
    payload = client._get(
        "https://serpapi.com/search.json",
        {**BASE, "engine": "google_flights", "departure_id": "STL", "arrival_id": "CDG",
         "outbound_date": "2027-06-05", "return_date": "2027-06-14", "type": 1,
         "adults": 2, "children": 4},
    )
    print("top-level keys:", sorted(payload.keys()))
    if payload.get("error"):
        print("error:", payload["error"])
    insights = payload.get("price_insights") or {}
    hist = insights.get("price_history") or []
    print("price_insights keys:", sorted(insights.keys()))
    print("lowest:", insights.get("lowest_price"), "level:", insights.get("price_level"),
          "typical range:", insights.get("typical_price_range"))
    print("history points:", len(hist), "first:", hist[:2], "last:", hist[-2:])
    for group in ("best_flights", "other_flights"):
        rows = payload.get(group) or []
        print(group, len(rows), [r.get("price") for r in rows[:6]])
        if rows:
            first = rows[0]
            legs = first.get("flights") or []
            print("  first option:", first.get("price"), first.get("type"),
                  [(leg.get("airline"), (leg.get("departure_airport") or {}).get("id"),
                    (leg.get("arrival_airport") or {}).get("id")) for leg in legs])
except Exception as exc:  # noqa: BLE001
    print("FAILED:", exc)

print("\nsearches left:", client.searches_left())
