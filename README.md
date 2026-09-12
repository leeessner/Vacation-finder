# Vacation deal tracker

Tracks the real, all-in cost of specific vacations you're considering — flights
plus lodging plus ground transport — every day, builds a price history, and
emails you when something is genuinely a good deal rather than merely cheap-
sounding.

Runs entirely on free tiers: GitHub Actions for scheduling, Gmail SMTP for
email. No subscriptions.

**[Setup instructions →](SETUP.md)**

## The idea

"$3,900 for a week in Cancún" tells you nothing on its own. It means something
once you know the same trip has ranged $3,600–$5,400 over four months and today
sits in the cheapest 8% of everything observed.

So the tracker:

1. Prices each trip you define, every day, across several candidate departure
   dates and all your home airports.
2. Records the cheapest complete option to a CSV — one row per day, committed to
   git, so the price history is permanent and diffable.
3. Judges today's number against that history (percentile, all-time low, change
   versus the 30-day median) instead of against your gut.
4. Emails you a digest on a schedule, and an alert the moment a threshold
   you set is crossed.
5. Suggests destinations you aren't tracking that match the tags on the ones you
   are, with ready-to-paste config for each.

## What it costs to run

| Piece | Service | Cost |
|---|---|---|
| Scheduling | GitHub Actions | Free (~5 min/day, well inside the free allowance) |
| Flights & hotels | Travelpayouts + Hotellook | Free affiliate token, no card |
| Flight cross-check | Google Flights scraper | Free, no key |
| Email | Gmail SMTP | Free |
| Storage & dashboard | Git + GitHub Pages | Free |

`python -m vacation_finder validate` prints your projected call volume, and
`python -m vacation_finder doctor` makes one live call to each source and
shows exactly what came back — the fastest way to confirm a working setup.

## Where the prices come from

**Travelpayouts** is the backbone, covering flights and hotels on one free
affiliate token. Two properties of it shape everything downstream:

- The data is **cached**, not live — the cheapest fares and rates other
  people's recent searches turned up. For watching a trend over months that
  is fine, arguably steadier than live search. For booking it is a pointer,
  not a quote.
- Flight prices are **per adult**; the endpoints take no passenger counts.
  The tracker scales by party size, which overstates slightly since children
  fly for less.

This replaced **Amadeus Self-Service**, whose free tier was decommissioned on
17 July 2026.

**A Google Flights scraper** (`fast-flights`) runs alongside for airfare.
It needs no key, and unlike Travelpayouts it is *live* and prices the actual
party rather than scaling one adult — so it is the better number when it
works. It is
also, unavoidably, fragile: when Google changes their page it stops working. So
it is strictly optional — if it fails, the run continues on cached data alone
and the failure is logged. When the two sources disagree by more than 15%, the
run notes it: some gap is expected between a live scrape and a per-adult
cached average, but a large one usually means the cache is stale or a source
has drifted.

### Known limits, stated plainly

- **Prices are cached, not live.** Travelpayouts reports what recent searches
  found, so a number can be hours or days stale. The tracker surfaces the
  fare's age when it is three days or older.
- **Flight prices are scaled from one adult.** The API takes no passenger
  counts, so a party of six is one adult fare times six. Real family fares are
  usually a little lower.
- **Southwest publishes fares nowhere.** Not to Travelpayouts, not to Google
  Flights, not to any aggregator. If Southwest flies your route, check by hand.
- **Hotel coverage varies by destination.** Set `lodging.coverage` per trip
  (`good`/`thin`/`none`) and the reports will say how much to trust the number
  rather than presenting all of them with equal confidence.
- **Vacation rentals (Airbnb/Vrbo) aren't covered.** They have no free API and
  aggressive bot protection.
- **Car rental and fuel are estimates, not quotes.** Set `ground.per_day_cost`
  or `ground.drive_round_trip_miles` from what you know; no free API prices a
  rental car or a road trip.
- **One gateway per trip.** A Barcelona-and-Madrid trip is tracked as the
  Barcelona flight and hotel, with the Madrid leg as a `ground.flat_cost`
  train estimate. Listing both cities as destinations would make the tracker
  treat them as alternatives and report the cheaper one.
- **Six travellers need two rooms.** `lodging.rooms` must reflect that or the
  prices are fiction; `validate` warns when a party won't fit.
- **Prices are indicative.** They're the cheapest option found for your filters
  at one moment each day. Always verify before booking.

## Defining a trip

Everything lives in [`config/trips.yaml`](config/trips.yaml), which is fully
commented. The essentials:

```yaml
trips:
  - id: caribbean-winter-escape      # becomes data/history/<id>.csv — keep it stable
    name: "Riviera Maya all-inclusive"
    why: "Direct flight, kids' club, a week of sun in the worst of winter"
    destinations:
      - {code: CUN, label: "Cancún", city_code: CUN}
    party:
      adults: 2
      children: [8, 5]               # ages at travel time, not a count
    dates:
      mode: window                   # flexible — the tracker samples across it
      earliest: 2027-02-01
      latest: 2027-03-15
      nights: 7
    budget:
      target_total: 5200
    alerts:
      total_below: 4600              # email me the moment it's under this
      drop_pct_vs_median: 12         # ...or 12% below its own 30-day median
      all_time_low: true             # ...or cheaper than we've ever seen
    tags: [beach, all-inclusive, kid-friendly, warm-winter]
```

`tags` drive the suggestion engine: they're matched against
[`config/destinations.yaml`](config/destinations.yaml), a catalog you can edit
freely.

## Commands

```bash
python -m vacation_finder validate          # check config, estimate API usage
python -m vacation_finder track             # price everything, record, alert
python -m vacation_finder digest            # send the scheduled summary
python -m vacation_finder dashboard         # rebuild docs/index.html
python -m vacation_finder doctor            # live-check every price source
python -m vacation_finder demo --days 60    # synthetic history, to preview output
```

Add `--dry-run` to any command that sends mail to write the HTML to `build/`
instead.

## Schedule

| Workflow | When | What |
|---|---|---|
| `track.yml` | Daily, 14:00 UTC (= 9am Central) | Prices everything, commits history, sends alerts |
| `digest.yml` | Wed & Sat 01:00 UTC (= Tue & Fri 8pm Central) | Sends the summary email |
| `tests.yml` | On push | Runs the test suite |

Both schedules are one-line cron changes at the top of the workflow files.
Cron in GitHub Actions is always UTC.

## How the pieces fit

```
config/trips.yaml ─┐
                   ├─→ pricing.py ──→ sources/{travelpayouts,google_flights}.py
config/destinations.yaml                      │
                                              ▼
                            storage.py ──→ data/history/*.csv  (committed daily)
                                              │
                        ┌─────────────────────┼──────────────────────┐
                        ▼                     ▼                      ▼
                    stats.py             dashboard.py           alerts.py
              "is this good?"          docs/index.html      thresholds + cooldown
                        └─────────────────────┬──────────────────────┘
                                              ▼
                                       email_report.py
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

93 tests covering date sampling, driving trips, price assessment, alert rules and cooldowns,
config validation, storage round-trips, and API response parsing against
recorded payload shapes. No network access required.
