#!/usr/bin/env python3
"""Watch Apple's US Certified Refurbished store for M4 / M4 Pro Mac minis.

Standard library only. Usage:
    python3 refurb_tracker.py list            # show current matching listings (no state change)
    python3 refurb_tracker.py check           # one poll: diff against state, alert on changes
    python3 refurb_tracker.py loop            # poll forever (interval + jitter from config)
    python3 refurb_tracker.py test-notify     # send a test alert through configured notifiers

How Apple exposes the data: every refurb grid page embeds a JSON blob assigned
to `window.REFURB_GRID_BOOTSTRAP`, with a `tiles` array (title, partNumber,
productDetailsUrl, price.currentPrice.raw_amount, filters.dimensions.*).
When no Mac minis are in stock, /shop/refurbished/mac/mac-mini 301-redirects
to /shop/refurbished/mac, so we always fetch the all-Macs grid and filter it.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import notifiers
from tls import ssl_context

HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("REFURB_CONFIG", HERE / "config.json"))
STATE_PATH = Path(os.environ.get("REFURB_STATE", HERE / "state.json"))

GRID_URL = "https://www.apple.com/shop/refurbished/mac"
BASE = "https://www.apple.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
# A listing must be missing this many consecutive polls before it counts as
# gone. Apple's grid flickers; without this a flicker would fire a "restock".
GONE_AFTER_MISSES = 3

DEFAULT_CONFIG = {
    "filters": {
        "chips": ["M4", "M4 Pro"],   # any subset of ["M4", "M4 Pro"]
        "min_memory_gb": None,
        "min_storage_gb": None,
        "max_price": None,
    },
    "alert_on": {"new": True, "restock": True, "price_drop": True},
    "poll": {"interval_seconds": 300, "jitter_seconds": 60},
    "notifiers": [{"type": "console"}],
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


# ---------------------------------------------------------------- fetch/parse

def fetch_grid(url: str = GRID_URL, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_bootstrap(page: str) -> dict:
    marker = page.find("window.REFURB_GRID_BOOTSTRAP")
    if marker < 0:
        raise ValueError("REFURB_GRID_BOOTSTRAP not found (page layout changed or request blocked)")
    start = page.index("{", marker)
    obj, _ = json.JSONDecoder().raw_decode(page[start:])
    if not isinstance(obj.get("tiles"), list):
        raise ValueError("bootstrap JSON has no tiles array")
    return obj


_TAG = re.compile(r"<[^>]+>")


def _clean_title(t: str) -> str:
    # Sale tiles embed '<span class="visuallyhidden">Now </span>' in the title.
    t = _TAG.sub("", t)
    t = re.sub(r"\bNow\s*$", "", t)
    return " ".join(html.unescape(t).replace("‑", "-").split())


def _size_gb(v: str | None) -> int | None:
    if not v:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(gb|tb)", v.strip().lower())
    if not m:
        return None
    n = float(m.group(1))
    return int(n * 1024 if m.group(2) == "tb" else n)


def _chip(title: str) -> str | None:
    if re.search(r"\bM4 Pro\b", title):
        return "M4 Pro"
    if re.search(r"\bM4 Max\b", title):
        return "M4 Max"
    if re.search(r"\bM4\b", title):
        return "M4"
    return None


def parse_listings(boot: dict) -> list[dict]:
    """All Mac mini tiles on the grid, normalized (no chip filtering yet)."""
    out = []
    for t in boot["tiles"]:
        dims = (t.get("filters") or {}).get("dimensions") or {}
        title = _clean_title(t.get("title", ""))
        is_mini = dims.get("refurbClearModel") == "macmini" or "mac mini" in title.lower()
        if not is_mini:
            continue
        price_raw = (((t.get("price") or {}).get("currentPrice")) or {}).get("raw_amount")
        url = t.get("productDetailsUrl") or ""
        url = BASE + url.split("?")[0] if url.startswith("/") else url.split("?")[0]
        out.append({
            "part": t.get("partNumber") or (t.get("omnitureModel") or {}).get("partNumber"),
            "title": title,
            "chip": _chip(title),
            "memory_gb": _size_gb(dims.get("tsMemorySize")),
            "storage_gb": _size_gb(dims.get("dimensionCapacity")),
            "price": float(price_raw) if price_raw else None,
            "url": url,
        })
    return [x for x in out if x["part"]]


def apply_filters(listings: list[dict], f: dict) -> list[dict]:
    chips = set(f.get("chips") or ["M4", "M4 Pro"])
    keep = []
    for x in listings:
        if x["chip"] not in chips:
            continue
        # Unknown values pass: better a borderline alert than a missed one.
        if f.get("min_memory_gb") and x["memory_gb"] is not None and x["memory_gb"] < f["min_memory_gb"]:
            continue
        if f.get("min_storage_gb") and x["storage_gb"] is not None and x["storage_gb"] < f["min_storage_gb"]:
            continue
        if f.get("max_price") and x["price"] is not None and x["price"] > f["max_price"]:
            continue
        keep.append(x)
    return sorted(keep, key=lambda x: (x["price"] or 0))


def fmt_size(gb: int | None) -> str:
    if gb is None:
        return "?"
    return f"{gb // 1024}TB" if gb >= 1024 and gb % 1024 == 0 else f"{gb}GB"


def fmt_listing(x: dict) -> str:
    price = f"${x['price']:,.0f}" if x["price"] is not None else "$?"
    return (f"{price} | {x['chip']} | {fmt_size(x['memory_gb'])} RAM | "
            f"{fmt_size(x['storage_gb'])} SSD | {x['part']}\n  {x['title']}\n  {x['url']}")


# ---------------------------------------------------------------- state/diff

def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            log(f"state file {STATE_PATH} unreadable; starting fresh")
    return {"initialized": False, "items": {}}


def save_state(state: dict) -> None:
    # Atomic write so a crash mid-write can't corrupt state and re-fire alerts.
    fd, tmp = tempfile.mkstemp(dir=STATE_PATH.parent, prefix=".state.", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def diff(state: dict, current: list[dict], now: str) -> list[tuple[str, dict, dict | None]]:
    """Update state in place; return [(event, listing, previous_record)].

    State only changes on real events (new/gone/restock/price/miss count), never
    per poll, so committing it from GitHub Actions does not create a commit
    every run.
    """
    items = state.setdefault("items", {})
    events = []
    seen_now = set()
    for x in current:
        seen_now.add(x["part"])
        rec = items.get(x["part"])
        if rec is None:
            events.append(("new", x, None))
            items[x["part"]] = {**x, "first_seen": now, "misses": 0, "present": True}
            continue
        prev = dict(rec)
        if not rec.get("present", True):
            events.append(("restock", x, prev))
        elif (x["price"] is not None and rec.get("price") is not None and x["price"] < rec["price"]):
            events.append(("price_drop", x, prev))
        rec.update(x, misses=0, present=True)
        rec.pop("gone_since", None)
    for part, rec in items.items():
        if part in seen_now:
            continue
        rec["misses"] = rec.get("misses", 0) + 1
        if rec["misses"] >= GONE_AFTER_MISSES and rec.get("present", True):
            rec["present"] = False
            rec["gone_since"] = now
    return events


def event_message(event: str, x: dict, prev: dict | None) -> tuple[str, str]:
    price = f"${x['price']:,.0f}" if x["price"] is not None else "$?"
    spec = f"{x['chip']} {fmt_size(x['memory_gb'])}/{fmt_size(x['storage_gb'])}"
    if event == "new":
        title = f"New refurb Mac mini: {spec} {price}"
    elif event == "restock":
        title = f"Back in stock: Mac mini {spec} {price}"
    else:
        title = f"Price drop: Mac mini {spec} ${prev['price']:,.0f} -> {price}"
    return title, f"{x['title']}\n{x['url']}"


# ---------------------------------------------------------------- commands

def current_matches(cfg: dict) -> list[dict]:
    boot = extract_bootstrap(fetch_grid())
    if not boot["tiles"]:
        # An empty Mac grid means a block/outage, never "Apple sells no Macs".
        raise ValueError("grid returned zero tiles")
    return apply_filters(parse_listings(boot), cfg["filters"])


def cmd_list(cfg: dict) -> int:
    boot = extract_bootstrap(fetch_grid())
    minis = parse_listings(boot)
    matches = apply_filters(minis, cfg["filters"])
    print(f"Apple refurb Mac grid: {len(boot['tiles'])} listings total, "
          f"{len(minis)} Mac mini (any chip), {len(matches)} matching your filters "
          f"(chips={cfg['filters'].get('chips')}).")
    for x in matches:
        print(fmt_listing(x))
    if not matches:
        print("No matching M4 Mac minis listed right now.")
    return 0


def cmd_check(cfg: dict) -> int:
    try:
        matches = current_matches(cfg)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        # Fail quietly: log, keep state untouched, try again next poll.
        log(f"poll failed, skipping: {e}")
        return 0
    state = load_state()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    first_run = not state.get("initialized")
    events = diff(state, matches, now)
    state["initialized"] = True
    alert_on = cfg["alert_on"]
    to_send = [e for e in events if alert_on.get(e[0], False)]
    for ev, x, prev in to_send:
        title, body = event_message(ev, x, prev)
        notifiers.send_all(cfg["notifiers"], title, body, x["url"])
        log(f"ALERT {ev}: {title}")
    if first_run and not to_send:
        log("first run: no matching listings yet; baseline saved")
    log(f"poll ok: {len(matches)} matching, {len(to_send)} alert(s)")
    # Save only after alerts went out, so a crash re-alerts rather than drops.
    save_state(state)
    return 0


def cmd_loop(cfg: dict) -> int:
    p = cfg["poll"]
    interval = max(120, int(p.get("interval_seconds", 300)))  # floor: be polite
    jitter = max(0, int(p.get("jitter_seconds", 60)))
    log(f"looping every {interval}s ±{jitter}s")
    while True:
        cmd_check(load_config())
        time.sleep(interval + random.uniform(-jitter, jitter))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "check", "loop", "test-notify"])
    ap.add_argument("--jitter-start", type=int, default=0,
                    help="sleep a random 0..N seconds first (spreads scheduled runs)")
    a = ap.parse_args()
    cfg = load_config()
    if a.jitter_start:
        time.sleep(random.uniform(0, a.jitter_start))
    if a.command == "list":
        return cmd_list(cfg)
    if a.command == "check":
        return cmd_check(cfg)
    if a.command == "loop":
        return cmd_loop(cfg)
    notifiers.send_all(cfg["notifiers"], "Refurb tracker test", "If you see this, alerts work.", GRID_URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
