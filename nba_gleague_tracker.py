#!/usr/bin/env python3
"""
nba_gleague_tracker.py  -  Track NBA G League transactions and log new ones.

The gleague.nba.com/transactions page is JavaScript-rendered, but the data comes from the same
player-movement JSON the NBA uses, on the G League stats host. This fetches that feed, and any
transaction not seen before is appended to nba_gleague_snapshots/gleague_log.csv, which
nba_changelog.py folds into the same report (its own "G LEAGUE" source).

First run saves a baseline silently, like the other trackers.

USAGE
-----
    python nba_gleague_tracker.py            # one pass
    python nba_gleague_tracker.py --test     # dry run: counts only, don't log
    python nba_gleague_tracker.py --silent   # no toast

Exposes poll_once() and send_test() so nba_tracker_all.py drives it with the others.
"""

import os
import sys
import csv
import gzip
import zlib
import json
import datetime
import urllib.request

try:
    from winotify import Notification, audio
    HAVE_TOAST = True
except Exception:
    HAVE_TOAST = False

BASE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(BASE, "nba_gleague_snapshots")
os.makedirs(SNAP, exist_ok=True)
LOG = os.path.join(SNAP, "gleague_log.csv")
SEEN = os.path.join(SNAP, "seen.json")

# G League transactions feed (confirmed from the site's own network request).
FEED_URL = "https://cdn-gleague.nba.com/static/json/staticData/GLeagueTransactions.json"


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (nba-gleague-tracker)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
    if enc == "gzip" or raw[:2] == bytes([0x1f, 0x8b]):
        raw = gzip.decompress(raw)
    elif enc == "deflate":
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw.decode("utf-8-sig", "replace").strip()


def _pretty(slug):
    if not slug:
        return ""
    return " ".join(w.capitalize() for w in str(slug).replace("_", " ").split("-"))


def parse_feed(text):
    data = json.loads(text)
    # feed is a plain array; stay tolerant of a wrapped form too
    if isinstance(data, dict):
        for k in ("GLeagueTransactions", "NBA_Player_Movement", "transactions", "rows"):
            if k in data:
                data = data[k]
                break
        if isinstance(data, dict):
            data = data.get("rows", [])
    rows = data if isinstance(data, list) else []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        date = (r.get("TRANSACTION_DATE") or "")[:10]
        typ = (r.get("TRANSACTION_TYPE") or r.get("Transaction_Type") or "").strip()
        action = (r.get("TRANSACTION_DESCRIPTION") or typ or "").strip()
        player = _pretty(r.get("PLAYER_SLUG"))
        team = _pretty(r.get("TEAM_SLUG"))
        gid = str(r.get("GROUP_SORT") or r.get("GroupSort") or "")
        pid = str(r.get("PLAYER_ID") or "")
        if player and team:
            desc = f"{action}: {player} ({team})"
        elif player:
            desc = f"{action}: {player}"
        elif team:
            desc = f"{action}: {team}"
        else:
            desc = action
        if not desc:
            continue
        key = (gid + "|" + pid) if (gid or pid) else (date + "|" + desc)
        out.append({"key": key, "date": date, "type": typ, "desc": desc})
    return out


def load_seen():
    if os.path.exists(SEEN):
        try:
            return set(json.load(open(SEEN, encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(keys):
    json.dump(sorted(keys), open(SEEN, "w", encoding="utf-8"), ensure_ascii=False)


def append_log(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["detected_at", "date", "type", "description"])
        for r in rows:
            w.writerow(r)


def poll_once(silent=False, dry=False):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        raw = fetch(FEED_URL)
    except Exception as e:
        print(f"fetch failed: {e}")
        return
    try:
        txs = parse_feed(raw)
    except Exception as e:
        print(f"parse failed: {e}  (first 80 chars: {raw[:80]!r})")
        return

    seen = load_seen()
    if not seen:
        save_seen({t["key"] for t in txs})
        print(f"baseline saved: {len(txs)} G League transactions")
        return

    new = [t for t in txs if t["key"] not in seen]
    if dry:
        print(f"(test) {len(txs)} in feed, {len(new)} new since last snapshot")
        return

    if new:
        rows = [[now, t["date"], t["type"], t["desc"]] for t in reversed(new)]  # oldest first
        append_log(rows)
        save_seen(seen | {t["key"] for t in txs})
        print(f"{len(new)} new transaction(s): " + "; ".join(t["desc"][:60] for t in new[:3]))
        if HAVE_TOAST and not silent:
            try:
                n = Notification(app_id="NBA G League",
                                 title=f"G League: {len(new)} transaction(s)",
                                 msg=new[0]["desc"][:250])
                n.set_audio(audio.Default, loop=False)
                n.show()
            except Exception:
                pass
    else:
        print("no new transactions")


def send_test():
    """Sample notification (used by nba_tracker_all.py --test)."""
    if HAVE_TOAST:
        try:
            n = Notification(app_id="NBA G League", title="NBA G League Tracker",
                             msg="G League tracking is wired up and working.")
            n.set_audio(audio.Default, loop=False)
            n.show()
            print("sent sample toast")
        except Exception as e:
            print(f"toast failed: {e}")
    else:
        print("winotify not installed (pip install winotify) - no toast sent")


def main():
    poll_once(silent="--silent" in sys.argv, dry="--test" in sys.argv)


if __name__ == "__main__":
    main()
