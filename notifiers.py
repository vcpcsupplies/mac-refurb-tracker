"""Alert channels. Each notifier failure is logged and swallowed so one broken
channel never blocks the others or crashes a poll."""
from __future__ import annotations

import os
import sys
import urllib.request

from tls import ssl_context


def _console(cfg: dict, title: str, body: str, url: str) -> None:
    print(f"*** {title}\n{body}", flush=True)


def _ntfy(cfg: dict, title: str, body: str, url: str) -> None:
    # The topic is effectively the password: anyone who knows it can read and
    # post. Keep it out of the repo -> NTFY_TOPIC env var / GitHub secret.
    topic = os.environ.get("NTFY_TOPIC") or cfg.get("topic")
    if not topic:
        raise RuntimeError("ntfy topic not set (NTFY_TOPIC env var or notifiers[].topic)")
    server = (os.environ.get("NTFY_SERVER") or cfg.get("server") or "https://ntfy.sh").rstrip("/")
    headers = {
        # HTTP headers must be latin-1; titles are built ASCII-only anyway.
        "Title": title.encode("ascii", "replace").decode(),
        "Click": url,
        "Priority": str(cfg.get("priority", "high")),
        "Tags": "computer",
    }
    req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=ssl_context()) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"ntfy HTTP {resp.status}")


CHANNELS = {"console": _console, "ntfy": _ntfy}


def send_all(configs: list[dict], title: str, body: str, url: str) -> None:
    for c in configs:
        fn = CHANNELS.get(c.get("type"))
        if fn is None:
            print(f"unknown notifier type: {c.get('type')}", file=sys.stderr)
            continue
        try:
            fn(c, title, body, url)
        except Exception as e:  # noqa: BLE001 - never let alerting crash a poll
            print(f"notifier {c.get('type')} failed: {e}", file=sys.stderr)
