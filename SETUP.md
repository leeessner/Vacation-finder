# Setup

Three one-time steps, about 15 minutes. Everything here is free — no
subscriptions, no credit card, no per-call charges.

> **Note on history:** this was originally built on the Amadeus Self-Service
> API (decommissioned 17 July 2026), then on Travelpayouts, whose signup
> requires a website with its affiliate script installed. Hotel prices now
> come from Google Hotels through SerpApi. Any other mention of Amadeus or
> Hotellook is stale.

---

## 1. SerpApi key, for hotel prices (free, ~3 min)

SerpApi runs Google searches and returns the results as data. Its free plan
covers Google Hotels at **250 searches a month**, with no card and no website.

1. Sign up at <https://serpapi.com/users/sign_up> — email and password, or
   Google sign-in. Confirm your email. If it asks you to verify a phone
   number, that's part of their normal anti-abuse check.
2. Choose the **Free** plan if asked.
3. Open <https://serpapi.com/manage-api-key> and copy your **API key**.

### What you are actually signing up for

- **250 searches a month, then it stops** until the month resets. It never
  charges you; there's no card on file. The tracker is built to fit: about
  7 searches a day (210 a month), keeping 10 spare for Doctor and manual
  runs, and it checks how many you have left before each run.
- **Each hotel price is re-checked every few days, not daily.** In between,
  the last price is reused and labelled with its age. For stays months out,
  that's plenty.
- **The prices are Google Hotels' prices** for your real party: two rooms of
  one adult and two children each, or one rental for all six.

### Travelpayouts (optional, skip it)

The tracker can also read Travelpayouts' cached flight fares, but nothing
needs it: airfare comes from the Google Flights scraper, which needs no
account. Only add `TRAVELPAYOUTS_TOKEN` if you already have one.

## 2. GitHub secret (~2 min)

In this repository: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret name | Value |
|---|---|
| `SERPAPI_KEY` | API key from step 1 |

That is the only secret. Updates need none (next step).

## 3. Updates by email (nothing to sign up for)

Each digest (Tuesday and Friday evenings) and each deal alert is opened as an
issue in this repository, assigned to the repository owner. GitHub emails you
about issues assigned to you, so the update lands in your inbox with no mail
account, password or API key anywhere — the workflow uses GitHub's own token,
which expires when the run ends. The previous digest is closed when a new one
opens.

To make sure the emails arrive:

1. Open <https://github.com/settings/notifications>.
2. Under **Participating, @mentions and custom**, make sure **Email** is ticked
   (it is by default).
3. Under **Default notifications email**, check it's the address you want.

The update is plain text: each trip's price today, which way it's moving,
what to do, and links to the dashboard (charts) and spreadsheet (every price).
It's in the repository's public Issues tab too — nothing in it that isn't
already on the public dashboard.

### Sending it to a second person

GitHub only emails GitHub accounts, so the simplest way to include someone
else is a Gmail rule that forwards these emails automatically. Nothing about
them goes into this (public) repository.

1. In Gmail: **Settings (gear) → See all settings → Forwarding and POP/IMAP →
   Add a forwarding address**. Enter their address. Gmail emails them a
   confirmation link; once they click it, come back and refresh.
   Leave "Disable forwarding" selected — the rule below does the forwarding.
2. In the Gmail search box, search:
   `from:notifications@github.com subject:"Vacation watch"`
   then click the **Show search options** icon at the right of the box, and
   **Create filter**. (Digests and deal alerts both start "Vacation watch", so
   this catches them and nothing else from GitHub.)
3. Tick **Forward it to** and choose their address. Click **Create filter**.

> **Why not a Gmail app password?** An earlier version of this guide sent mail
> through Gmail with an app password, and said that password "only grants mail
> sending". That was wrong: an app password can read the whole mailbox as well
> as send. Since the tracker runs third-party scraping code in the same job,
> that risk bought nothing, and the design was replaced. If you created one,
> revoke it at <https://myaccount.google.com/apppasswords>.

## 4. Turn on the dashboard (~2 min)

**Settings → Pages → Source: Deploy from a branch**, branch
`claude/vacation-deal-tracker-ha5wot` (or `main` once merged), folder `/docs`.

Your dashboard will be at `https://<your-username>.github.io/Vacation-finder/`,
and that link is included in every email.

> Pages sites are public even when the repository is private. The dashboard
> shows your destinations, travel dates and budgets. If you'd rather not
> publish that, skip this step — the emails and the CSVs work without it.

---

## Verify it works — do this first

From the **Actions** tab, run **Doctor** (*Run workflow*). It uses about
three of the month's searches and prints exactly what came back, so you find
out in 30 seconds rather than at 9am tomorrow.

It shows: that the key works and how many searches are left; the cheapest
hotels Google returns for your first trip, each with its distance from the
trip's `center`, and which one the tracker would record under your rules
(with a link to check it on Google); the preferred-brand search (Embassy
Suites); Google Flights' verdict and typical price range for the first flying
trip; and whether the Google Flights scraper works.

Then run **Track prices** manually. You should see:

- `SerpApi: N search(es) left this month` and `hotel searches this run: 7 of 26 slot(s)`.
- Lines like `best USD 1597 (2027-03-12 → 2027-03-16)`.
- A new commit with the day's prices, including `data/state/lodging.json`
  (the hotel-price cache).

It takes about four daily runs before every trip has a hotel price. Until a
trip has one, its total is marked incomplete and isn't judged or alerted on.

If the log says `SERPAPI_KEY is not set`, the secret is missing or misnamed.
If Doctor says nothing meets the trip's lodging rules, loosen `min_stars` or
`max_per_night`, or check that `hotel_query` is a real place.

## Seeing the results

Three places, all updated after every daily run:

- **Email** — the digest (Tuesday and Friday evenings) and instant deal
  alerts. Needs nothing set up; see step 3.
- **Dashboard** — a web page with a chart per trip (flights, hotel and total
  as separate lines), the outlook, and the last two weeks of checks. Needs
  step 4 (GitHub Pages).
- **Spreadsheet** — `data/prices-over-time.csv` in the repository: one row
  per day, flights / hotel / total for every trip. Click it on GitHub to see
  it as a table, or download it into Excel or Google Sheets. The dashboard
  links to it.

## Quick command reference

```bash
pip install -r requirements-dev.txt

python -m vacation_finder validate           # check config, show hotel-search cadence
python -m vacation_finder doctor             # live-check every price source (1 search)
python -m vacation_finder demo --days 60     # synthetic data, to preview output
python -m vacation_finder track              # real prices (needs the env vars)
python -m vacation_finder digest --dry-run   # write the update to build/ instead of sending
```

Set credentials locally with a `.env`-style export before `track`:

```bash
export SERPAPI_KEY=...
```

---

## Working on this from your computer

You don't strictly need a local copy — the tracker runs entirely on GitHub's
servers, on a schedule, whether or not your laptop is on. But if you want it
alongside your other projects in `~/Development`, it's the usual clone:

```bash
cd ~/Development
git clone https://github.com/leeessner/Vacation-finder.git
cd Vacation-finder
git checkout claude/vacation-deal-tracker-ha5wot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

python -m vacation_finder validate
```

Then the normal loop — edit `config/trips.yaml`, commit, push:

```bash
git add config/trips.yaml
git commit -m "Add Greece to the summer list"
git push
```

### One habit worth forming: pull before you edit

This repository is unusual in that **it writes to itself.** The tracking
workflow commits a new price row every morning, so your local clone falls
behind daily. If you edit without pulling, your push is rejected and you'll
have to merge.

```bash
git pull        # do this first, every time
```

If you only ever edit `config/trips.yaml` and the bot only ever touches
`data/` and `docs/`, a stale pull merges cleanly anyway — but pulling first
saves the annoyance.

### Editing the config without a local clone

You can also edit `config/trips.yaml` directly on github.com — open the file,
click the pencil, commit. For adding a destination or changing an alert
threshold that's often quicker, and it sidesteps the pull-first problem
entirely.
