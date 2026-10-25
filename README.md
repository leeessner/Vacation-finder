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
4. Shows which way each price is moving — flights and hotel separately — and
   gives an **outlook** for each trip: *Good time to book*, *Book soon*,
   *Keep watching* or *Wait if you can*, with its reasons and how much
   evidence it rests on (see "The outlook" below).
5. Emails you a digest on a schedule, and an alert the moment a threshold
   you set is crossed.
6. Keeps one spreadsheet of every trip's prices over time,
   `data/prices-over-time.csv` — a row per day, flights / hotel / total per
   trip, ready for Excel or Google Sheets.
7. Suggests destinations you aren't tracking that match the tags on the ones you
   are, with ready-to-paste config for each.

### House rules for lodging

Set per trip in `config/trips.yaml`:

- **Guest rating 4.0+** (`min_rating`) and 3-star class or better.
- **Close to what you'd do.** Each destination has a `center` and
  `within_miles`; anything farther out is ignored. The cheapest hotel "in
  Nashville" is nine miles from downtown, and staying close in costs roughly
  twice as much there — the tracker shows that honestly rather than tracking
  a price you'd never pay.
- **One room for six in the US** (`rooms: 1`): Google only returns rooms that
  fit the whole party, which in practice means suites. Two rooms in Europe.
- **A preferred brand** (`prefer: ["Embassy Suites"]`) is tracked whenever one
  qualifies, with the cheapest alternative noted beside it. One extra search
  per check.
- **Cabins and rentals** (`kind: rental`) only where that's the point of the
  trip.
- **Drive, don't fly, within six hours** (`settings.max_drive_hours`):
  `validate` warns about a flying trip whose destination is a shorter drive,
  and suggestions for nearby cities come back configured as drives.

## What it costs to run

| Piece | Service | Cost |
|---|---|---|
| Scheduling | GitHub Actions | Free (~5 min/day, well inside the free allowance) |
| Hotels & rentals | Google Hotels via SerpApi | Free key, 250 searches/month, no card |
| Flights | Google Flights scraper | Free, no key |
| Flight cross-check (optional) | Travelpayouts | Free affiliate token, if you have one |
| Email | Gmail SMTP | Free |
| Storage & dashboard | Git + GitHub Pages | Free |

`python -m vacation_finder validate` prints your projected hotel-search volume
against the free 250, and `python -m vacation_finder doctor` makes one live
call to each source and shows exactly what came back — the fastest way to
confirm a working setup.

## Where the prices come from

**Google Hotels**, through SerpApi, prices lodging. Google aggregates
Booking.com, Expedia, Hotels.com, the hotels' own sites and vacation rentals,
and it prices the real guests: adults plus each child's age. A hotel stay for
six is priced as `lodging.rooms` identical rooms, each holding its share of
the party; a trip with `lodging.kind: rental` is priced as one cabin or
apartment for everyone.

The free plan is **250 searches a month**, shared with the weekly flight
check below, so hotel prices are not re-checked daily. Each run spends up to
7 searches: flying trips due their weekly check first, then the hotel
"slots" (trip × date window) that have gone longest without a check. The last
price is reused for the rest, for up to 10 days, and the reports say how old
it is. With the current trips each hotel price is re-checked about every five
days — plenty for stays months out. `lodging.py` explains the budgeting;
`validate` prints the cadence.

**A Google Flights scraper** (`fast-flights`) prices airfare daily for every
date window. It needs no key, is live, and prices the actual party. It is
also, unavoidably, fragile: when Google changes their page it stops working.

**Google Flights through SerpApi**, once a week per flying trip, adds
Google's own verdict — *low*, *typical* or *high* — and the typical price
range for the route and dates ($7,800–$9,400 for six to Paris in June, when
this was written). That is the one piece of price context that doesn't have
to be built up day by day. Its fare also stands in for a day when the scraper
fails. Google returns no price *history* for trips this far out (checked
live), so trends come from the tracker's own daily prices.

### The outlook

Nobody can predict next month's fare, and the outlook doesn't pretend to. It
combines the three things that genuinely help (`forecast.py`):

1. Where today's fare sits against Google's typical range.
2. Which way the tracker's own prices have been moving (a least-squares
   trend over the last four weeks, from seven days of data on).
3. How far away the trip is, against when long-haul fares are usually lowest:
   **5–7 months before departure** (`settings.booking_window_months`, rough
   guidance from published booking studies). For June 2027 that's November
   to January.

Each outlook lists its reasons and a confidence — *low* until there are a few
weeks of tracking and a Google verdict, *high* after about two months. Drive
trips get a hotel-only outlook.

**Travelpayouts** cached fares are used alongside it *if* a
`TRAVELPAYOUTS_TOKEN` is set. It's optional — its signup demands a website
with its affiliate script installed. When both sources price a flight and
disagree by more than 15%, the run notes it.

This replaced **Amadeus Self-Service**, whose free tier was decommissioned on
17 July 2026, and then Travelpayouts' Hotellook for hotels.

### Known limits, stated plainly

- **Hotel prices are a few days old at most, not today's.** 250 free
  searches a month can't re-check every slot daily; a reused price is labelled
  with its age, and dropped once it's older than `hotel_price_max_age_days`.
- **A hotel stay is priced as identical rooms.** Two rooms at one hotel, each
  priced for three guests. A family suite might be cheaper; set
  `lodging.kind: rental` to price an apartment or house instead.
- **Southwest publishes fares nowhere.** Not to Google Flights, not to any
  aggregator. If Southwest flies your route, check by hand.
- **Coverage still varies by destination.** Set `lodging.coverage` per trip
  (`good`/`thin`/`none`) and the reports will say how much to trust the number
  — e.g. a Munich hotel standing in for an alpine resort is `thin`.
- **Days without a hotel price don't count.** A total with no lodging looks
  like a bargain; it's kept in the CSV but left out of every "is this a good
  price" judgement and every alert.
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
                   ├─→ pricing.py ──→ sources/{google_hotels,google_flights}.py
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
