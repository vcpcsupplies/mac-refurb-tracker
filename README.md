# Mac mini refurb tracker

Watches Apple's US Certified Refurbished store for **M4 / M4 Pro Mac minis** and
pushes a phone alert (ntfy), checking every 2 minutes from your Mac, when one is newly listed, comes back in stock, or
drops in price. Python 3 standard library only; no dependencies.

## How it works

- Fetches `https://www.apple.com/shop/refurbished/mac` (one request per poll)
  and reads the JSON Apple embeds as `window.REFURB_GRID_BOOTSTRAP`.
- Why not the `/mac/mac-mini` page? When no minis are in stock Apple redirects
  it to the all-Macs grid, so the tracker always reads the full grid and keeps
  tiles where the model is `macmini` or the title says "Mac mini".
- Per listing it records: title, chip, memory, storage, price, part number, URL.
- `state.json` remembers what's been seen. Alerts fire on:
  - **new**: part number never seen before
  - **restock**: was gone (missing 3 polls in a row), now back
  - **price_drop**: same part, lower price
- A listing that blinks out for one or two polls doesn't count as gone, so
  you don't get a fake "restock" alert.
- A network error, a blocked page, or an empty grid is logged and skipped. State
  stays as it was, so nothing gets marked gone because Apple hiccuped.

## Use it

```bash
python3 refurb_tracker.py list          # current M4 minis, no state change
python3 refurb_tracker.py check         # one poll: diff + alert
python3 refurb_tracker.py test-notify   # send a test push
python3 refurb_tracker.py loop          # poll forever locally (5 min ± 60 s)
python3 -m unittest -v test_tracker.py  # offline tests
```

ntfy needs `NTFY_TOPIC` set (the topic name works like a password, so keep it out
of the repo):

```bash
NTFY_TOPIC=your-topic python3 refurb_tracker.py test-notify
```

Phone: install the **ntfy** app (iOS/Android), tap **+**, subscribe to your
topic on `ntfy.sh`. Tapping an alert opens the Apple listing.

## Filters (`config.json`)

```jsonc
"filters": {
  "chips": ["M4", "M4 Pro"],   // ["M4 Pro"] for Pro only
  "min_memory_gb": 24,         // null = any
  "min_storage_gb": 512,       // 1TB = 1024
  "max_price": 1200
}
```

A listing with an unknown memory or storage value passes the filter. A
borderline alert is better than a missed one. `alert_on` turns each event type
on or off.

## Running on your Mac (launchd) — current setup

A per-user launchd agent runs `check` every 2 minutes (plus up to 30 s of random
delay) while the Mac is awake. If the Mac was asleep, launchd runs one check on
wake instead of all the missed ones. Nothing runs while the Mac is asleep or off.

Start (or restart after changing the interval or topic):

```bash
NTFY_TOPIC=your-topic ~/Documents/mac-refurb-tracker/launchd/install.sh
```

Stop and remove:

```bash
~/Documents/mac-refurb-tracker/launchd/uninstall.sh
```

Is it running? / what did it see?

```bash
launchctl print gui/$(id -u)/local.mac-refurb-tracker | grep -E "state =|runs =|last exit"
```

```bash
tail -f ~/Library/Logs/mac-refurb-tracker.log
```

- The agent's plist is `~/Library/LaunchAgents/local.mac-refurb-tracker.plist`
  (mode 600, since it holds the ntfy topic).
- State: `state.local.json` (gitignored), separate from the Actions
  `state.json`.
- Change the interval with `INTERVAL=180 NTFY_TOPIC=... ./launchd/install.sh`.
  Please don't go below 120 s.
- Python: whatever `python3` resolves to at install time. Pass `PYTHON=/path`
  to pick another one.

## Running on GitHub Actions (backup, disabled)

> **Currently disabled.** The tracker now runs on the Mac (see below), because
> GitHub's scheduler was 5–30 min late. Leave this off while launchd is running:
> the two keep separate state, so running both can send duplicate alerts.

`.github/workflows/tracker.yml` runs `check` every 5 minutes and commits
`state.json` back **only when it changes** (a listing appears or disappears, or
its price changes). The git history doubles as a log of listings.

Setup:
1. Push this folder to a GitHub repo.
2. Repo → Settings → Secrets and variables → Actions → new secret
   `NTFY_TOPIC` = your topic.
3. Actions tab → enable workflows → run **refurb-tracker** once with
   "Run workflow" to confirm it's green.

**Start / stop:** Actions → refurb-tracker → `···` → **Disable workflow** /
**Enable workflow**. Or from the CLI:

```bash
gh workflow disable refurb-tracker
```

```bash
gh workflow enable refurb-tracker
```

Caveats:
- GitHub's scheduler doesn't guarantee every 5 minutes. Runs are often 5–30
  minutes late when GitHub is busy, so expect alerts within minutes to half an
  hour.
- Minutes: each run bills about 1 minute, which comes to about 8,600 runs a month
  at `*/5`. That's free in a **public** repo. A **private** repo on the free plan
  gets 2,000 minutes a month, so there you'd need `*/30` or slower (edit the
  `cron` line). Nothing secret is in the repo (the topic is a secret), but a
  public repo shows the code and the state commits.
- GitHub disables scheduled workflows in a public repo after 60 days without
  activity. State commits count as activity, but if nothing is listed for 60
  days the workflow can still get turned off. GitHub emails you first, and
  re-enabling takes one click.

## Files

| File | What |
| --- | --- |
| `refurb_tracker.py` | fetch, parse, filter, diff, CLI |
| `notifiers.py` | `console`, `ntfy` channels |
| `tls.py` | CA bundle fallback for python.org Python builds |
| `config.json` | filters, alert types, poll interval, notifiers |
| `state.json` | seen listings (committed by Actions) |
| `state.local.json` | seen listings for the launchd agent (gitignored) |
| `launchd/` | install / uninstall the Mac agent |
| `test_tracker.py` | offline tests with a fake grid page |

Not yet verified against a live Mac mini tile, because none were listed while
this was built (2026-11-13). Matching falls back to the title, so if Apple's
model key isn't exactly `macmini`, tiles still match on "Mac mini" in the title.
