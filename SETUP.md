# Setup

Three one-time steps, about 20 minutes. Everything here is free — no
subscriptions, no credit card, no per-call charges.

> **Note on history:** this was originally built on the Amadeus Self-Service
> API, whose free tier was decommissioned on 17 July 2026. Travelpayouts
> replaces it. If you find any reference to Amadeus elsewhere, it is stale.

---

## 1. Travelpayouts token (free, ~8 min)

Travelpayouts is a travel affiliate network. The account is free and needs no
card, and the API token is issued immediately on signup.

1. Register at <https://www.travelpayouts.com/>. Email and password; no
   payment details are requested.
2. Confirm your email and finish the short profile. When it asks what you
   promote, a personal site or blog is a fine answer — you are not obliged to
   promote anything, and the API works regardless.
3. Go to **Tools → API** (or <https://www.travelpayouts.com/programs/100/tools/api>)
   and copy your **API token**.
4. While you are there, note your **marker** — the affiliate id shown in your
   dashboard. It is optional for our purposes but worth storing.

### What you are actually signing up for

Worth being clear, since it is a commercial network rather than a plain
developer portal:

- You are creating an **affiliate marketing account.** Nothing obliges you to
  use it as one, and the data API works without ever placing a link.
- The prices are **cached**, not live — they are the cheapest fares and rates
  that other people's recent searches turned up. For watching a trend over
  months, which is what this project does, that is fine and arguably steadier
  than live search. For booking, treat every number as a pointer, not a quote.
- Flight prices are quoted **per adult**. The API takes no passenger counts at
  all. The tracker multiplies by your party size, which overstates a little
  because children usually fly for less. Overstating is the safer direction
  for a budget, but it is an estimate and the reports say so.

## 2. Gmail app password (free, ~3 min)

Gmail will not accept your normal password over SMTP. You need an app password,
which requires 2-Step Verification on the account.

1. Turn on 2-Step Verification: <https://myaccount.google.com/signinoptions/two-step-verification>
2. Create an app password: <https://myaccount.google.com/apppasswords>
3. Name it "Vacation tracker". Google shows a 16-character password — copy it.
   Spaces in it don't matter; they're ignored.

An app password only grants mail sending. You can revoke it at any time from
that same page without touching your account password.

## 3. GitHub secrets (~5 min)

In this repository: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret name | Value |
|---|---|
| `TRAVELPAYOUTS_TOKEN` | API token from step 1 |
| `TRAVELPAYOUTS_MARKER` | Your affiliate marker (optional) |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASSWORD` | The 16-character app password from step 2 |
| `EMAIL_TO` | Where the reports go (can be the same address) |

Non-Gmail senders can additionally set the repository *variables* `SMTP_HOST`
and `SMTP_PORT`; they default to `smtp.gmail.com` and `587`.

## 4. Turn on the dashboard (optional, ~1 min)

**Settings → Pages → Source: Deploy from a branch**, branch
`claude/vacation-deal-tracker-ha5wot` (or `main` once merged), folder `/docs`.

Your dashboard will be at `https://<your-username>.github.io/Vacation-finder/`,
and that link is included in every email.

> Pages sites are public even when the repository is private. The dashboard
> shows your destinations, travel dates and budgets. If you'd rather not
> publish that, skip this step — the emails and the CSVs work without it.

---

## Verify it works — do this first

From the **Actions** tab, run **Doctor** (*Run workflow*). It makes one call to
each source and prints exactly what came back, so you find out in 30 seconds
rather than at 9am tomorrow.

It checks four things: the token is accepted; flight prices come back for a
real route; hotel prices come back for a real city; and the Google Flights
scraper still works.

### The one thing Doctor asks you to settle

Hotellook's cached price is ambiguous about whether it covers **one night** or
**the whole stay**, and the documentation does not say. Doctor prints both
readings with a real hotel name. Look that hotel up on Google for those dates,
see which figure it resembles, and set `hotel_price_is_per_night` in
`config/trips.yaml` to match. One minute, once, and every lodging number after
that is right.

Then run **Track prices** manually. You should see:

- A log line like `best USD 4231 (2027-03-12 → 2027-03-16)`.
- A new commit with the day's prices.
- A row in `data/history/<trip-id>.csv`.

If the log says `travelpayouts unavailable`, the token secret is missing or
wrong. If it says `no priceable option found`, the route may simply have no
cached fares — Doctor will show you whether any data exists at all.

## Quick command reference

```bash
pip install -r requirements-dev.txt

python -m vacation_finder validate           # check config, estimate API usage
python -m vacation_finder doctor             # live-check every price source
python -m vacation_finder demo --days 60     # synthetic data, to preview output
python -m vacation_finder track              # real prices (needs the env vars)
python -m vacation_finder digest --dry-run   # write the email to build/ instead of sending
```

Set credentials locally with a `.env`-style export before `track`:

```bash
export TRAVELPAYOUTS_TOKEN=...
export SMTP_USER=... SMTP_PASSWORD=... EMAIL_TO=...
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
