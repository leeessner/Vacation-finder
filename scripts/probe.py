"""Temporary: can Google Hotels find and price each family resort by name?"""
import json
import sys

sys.path.insert(0, "src")
from vacation_finder.sources.google_hotels import SerpApiClient, stay_price  # noqa: E402

RESORTS = ["Dachsteinkönig Familux Resort Gosau", "Oberjoch Familux Resort Bad Hindelang"]
client = SerpApiClient()
print("searches left:", client.searches_left())
for q in RESORTS:
    params = {"q": q, "check_in_date": "2027-06-19", "check_out_date": "2027-06-26",
              "adults": 2, "children": 2, "children_ages": "8,7",
              "currency": "USD", "gl": "us", "hl": "en"}
    print(f"\n=== {q}")
    try:
        p = client.search(params)
    except Exception as exc:  # noqa: BLE001
        print("  FAILED:", exc)
        continue
    props = p.get("properties")
    if props:
        print(f"  list of {len(props)}:")
        for x in props[:3]:
            print(f"   - {x.get('name')} | {x.get('type')} | stay {stay_price(x, 7)} | "
                  f"rating {x.get('overall_rating')} | class {x.get('extracted_hotel_class')}")
    else:
        keys = sorted(k for k in p.keys() if not k.startswith("search_") and k != "serpapi_pagination")
        print("  single property page; keys:", keys[:30])
        print(f"   name={p.get('name')} type={p.get('type')} stay={stay_price(p, 7)} "
              f"night={(p.get('rate_per_night') or {}).get('extracted_lowest')}")
        for offer in (p.get("prices") or [])[:4]:
            print("   offer:", json.dumps({k: offer.get(k) for k in ("source", "num_guests", "rate_per_night", "total_rate")})[:300])
        if p.get("featured_prices"):
            print("   featured:", json.dumps(p["featured_prices"][:2])[:500])
print("\nsearches left:", client.searches_left())
