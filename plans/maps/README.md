# Itinerary maps

One file per plan (same name as the plan, `.yaml`). The dashboard draws a
map from it on the trip's page, above "Day by day".

Each place:

    - {name: Bargello, days: [2], at: [43.7704, 11.2582], area: Florence}

- `days`: the itinerary days it's on (empty for a place on no particular day)
- `at`: latitude, longitude. Approximate, placed from the name
- `area`: which "zoom to" button it belongs to (usually the base)
- `kind`: `sight` (default), `stay`, `airport` or `option`
