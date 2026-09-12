# Vacation deal tracker

Tracks the real, all-in cost of specific vacations you're considering — flights
plus lodging plus ground transport — every day, builds a price history, and
emails you when something is genuinely a good deal rather than merely cheap-
sounding.

Runs entirely on free tiers: GitHub Actions for scheduling, the Amadeus
Self-Service API for prices, Gmail SMTP for email. No subscriptions.

**[Setup instructions →](SETUP.md)**

---

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
| Flights & hotels | Amadeus Self-Service | Free, 2,000 calls/month; default config uses ~700 |
| Flight cross-check | Google Flights scraper | Free, no key |
| Email | Gmail SMTP | Free |
| Storage & dashboard | Git + GitHub Pages | Free |

`python -m vacation_finder validate` prints your projected monthly call count
and warns before you'd exceed the quota.

## Where the prices come from

**Amadeus Self-Service** is the backbone — a real API with a genuinely free
tier, covering flights and chain hotels. It's stable and it won't break when
someone changes a web page.

**A Google Flights scraper** (`fast-flights`) runs alongside it for airfare.
It needs no key, and it sees fares Amadeus's inventory sometimes misses. It is
also, unavoidably, fragile: when Google changes their page it stops working. So
it is strictly optional — if it fails, the run continues on Amadeus alone and
the failure is logged. When the two sources disagree by more than 10%, the run
notes it, which is usually the first sign a source has drifted.

### Known limits, stated plainly

- **Southwest publishes fares nowhere.** Not to Amadeus, not to Google Flights,
  not to any aggregator. If Southwest flies your route, check it by hand.
- **Amadeus hotel coverage is chain-weighted.** Large brands are well covered;
  small independents, boutiques and many all-inclusives are not. For a specific
  resort you care about, put its `hotel_ids` in the trip config.
- **Vacation rentals (Airbnb/Vrbo) aren't covered.** They have no free API and
  aggressive bot protection.
- **Car rental is an estimate, not a quote.** Set `ground.per_day_cost` from
  what you know; there is no free rental API worth trusting.
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
python -m vacation_finder demo --days 60    # synthetic history, to preview output
```

Add `--dry-run` to any command that sends mail to write the HTML to `build/`
instead.

## Schedule

| Workflow | When | What |
|---|---|---|
| `track.yml` | Daily, 13:00 UTC | Prices everything, commits history, sends alerts |
| `digest.yml` | Wed & Sat 00:00 UTC (= Tue & Fri evening US Eastern) | Sends the summary email |
| `tests.yml` | On push | Runs the test suite |

Both schedules are one-line cron changes at the top of the workflow files.
Cron in GitHub Actions is always UTC.

## How the pieces fit

```
config/trips.yaml ─┐
                   ├─→ pricing.py ──→ sources/{amadeus,google_flights}.py
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

70 tests covering date sampling, price assessment, alert rules and cooldowns,
config validation, storage round-trips, and API response parsing against
recorded payload shapes. No network access required.
