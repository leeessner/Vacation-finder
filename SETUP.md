# Setup

Three one-time steps, about 20 minutes. Everything here is free — no
subscriptions, no credit card, no per-call charges.

---

## 1. Amadeus API keys (free tier, ~10 min)

Amadeus's Self-Service tier gives 2,000 free calls a month. The default config
uses roughly 700, so there is comfortable headroom.

1. Register at <https://developers.amadeus.com/register>. Email and password
   only — no payment details are requested at any point.
2. Confirm your email, then go to **My Self-Service Workspace → Create New App**.
3. Name it anything. You'll be shown an **API Key** and an **API Secret**.
4. Copy both. The secret is shown once.

> **Test vs production.** A new app starts in the *test* environment, which
> returns Amadeus's cached sample data. That is useful for confirming the
> plumbing works, but the prices are not real. When you're ready for live
> prices, click **Move to Production** in the Amadeus dashboard (still free,
> same 2,000-call quota) and set `amadeus_env: production` in
> `config/trips.yaml`. Until you do that, treat every number as fake.

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
| `AMADEUS_CLIENT_ID` | Amadeus API Key from step 1 |
| `AMADEUS_CLIENT_SECRET` | Amadeus API Secret from step 1 |
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

## Verify it works

From the **Actions** tab, run **Track prices** manually
(*Run workflow*). Then check:

- The run's log ends with a line like `best USD 4231 (2027-02-06 → 2027-02-13)`.
- A new commit appears with the day's prices.
- `data/history/<trip-id>.csv` has a row.

If the log says `amadeus unavailable`, the secrets aren't set or are wrong.
If it says `no priceable option found`, your date window or filters may be too
narrow — `max_stops: 0` with an unusual route is the usual culprit.

## Running it locally

```bash
pip install -r requirements-dev.txt

python -m vacation_finder validate           # check config, estimate API usage
python -m vacation_finder demo --days 60     # synthetic data, to preview output
python -m vacation_finder track              # real prices (needs the env vars)
python -m vacation_finder digest --dry-run   # write the email to build/ instead of sending
```

Set credentials locally with a `.env`-style export before `track`:

```bash
export AMADEUS_CLIENT_ID=... AMADEUS_CLIENT_SECRET=...
export SMTP_USER=... SMTP_PASSWORD=... EMAIL_TO=...
```
