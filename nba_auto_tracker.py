#!/usr/bin/env python3
"""
nba_auto_tracker.py  -  Hands-off tracking of the EXACT nba.com/players table.

nba.com/players is server-rendered: the full player list is embedded in the page's
__NEXT_DATA__ JSON (props.pageProps.players) - the very same data the page renders. We
fetch the page, read that JSON, and report exactly what changed in the table:

  ADDED    - a new row (player) appeared
  REMOVED  - a row disappeared
  CHANGED  - a cell changed for an existing player (team / number / position / height /
             weight / school / country), reported as  field: old -> new

No interpretation (no "trade"/"signing" guessing) - just the literal table diffs.

USAGE
-----
    python nba_auto_tracker.py                  # fetch live nba.com/players, diff, log
    python nba_auto_tracker.py --watch          # poll every 60s
    python nba_auto_tracker.py --watch --interval 30
    python nba_auto_tracker.py saved_page.html  # parse a saved page instead of fetching

Scheduled every minute via Task Scheduler (plain run) gives minute-resolution tracking.

OUTPUT (in ./nba_auto_snapshots)   -- its own folder, never collides with other trackers
----------------------------------
  state.json              - latest table, keyed by PERSON_ID
  changes_timeline.csv    - append-only: detected_at,type,id,name,field,old,new
  changes_timeline.jsonl  - same, one JSON object per line

NOTIFICATIONS (optional)
  * Windows toast:  pip install winotify   (DESKTOP_NOTIFY below is already True)
  * Discord/Slack:  set WEBHOOK_URL below or env var NBA_WEBHOOK
"""

import os
import re
import sys
import csv
import json
import time
import argparse
import datetime

URL = "https://www.nba.com/players"
WEBHOOK_URL = ""       # Discord/Slack webhook, or set env var NBA_WEBHOOK
DESKTOP_NOTIFY = True  # Windows toast on every change (needs: pip install winotify)

# Columns compared for CHANGED events (matches the nba.com/players table).
TRACK_FIELDS = ["name", "team", "number", "position", "height", "weight", "school", "country"]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

SNAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_auto_snapshots")
STATE_FILE = os.path.join(SNAP_DIR, "state.json")
TIMELINE_CSV = os.path.join(SNAP_DIR, "changes_timeline.csv")
TIMELINE_JSONL = os.path.join(SNAP_DIR, "changes_timeline.jsonl")


# ---------------------------------------------------------------- fetch/parse
def _extract_players(html):
    """Pull props.pageProps.players out of the page's __NEXT_DATA__ JSON."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found - page markup changed or the request "
                           "was blocked (try the saved-file mode, or the copy-paste tracker).")
    data = json.loads(m.group(1))
    try:
        arr = data["props"]["pageProps"]["players"]
    except (KeyError, TypeError):
        raise RuntimeError("players array not where expected in __NEXT_DATA__.")
    players = {}
    for p in arr:
        pid = str(p.get("PERSON_ID"))
        if not pid or pid == "None":
            continue
        players[pid] = {
            "id": pid,
            "name": f"{p.get('PLAYER_FIRST_NAME', '')} {p.get('PLAYER_LAST_NAME', '')}".strip(),
            "team": p.get("TEAM_ABBREVIATION") or "",
            "number": str(p.get("JERSEY_NUMBER") or ""),
            "position": p.get("POSITION") or "",
            "height": p.get("HEIGHT") or "",
            "weight": p.get("WEIGHT") or "",
            "school": p.get("COLLEGE") or "",
            "country": p.get("COUNTRY") or "",
        }
    return players


def fetch_players(source=None, retries=3):
    """From a live fetch (source=None) or a saved HTML file path."""
    if source:
        with open(source, encoding="utf-8", errors="replace") as f:
            return _extract_players(f.read())
    import urllib.request
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(URL, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", "replace")
            return _extract_players(html)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * attempt)
    raise RuntimeError(f"Could not fetch/parse nba.com/players after {retries} tries: {last}")


# ---------------------------------------------------------------- state + diff
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def diff_events(prev, cur, when):
    """Literal table diffs: added rows, removed rows, and per-cell changes."""
    events = []
    prev_ids, cur_ids = set(prev), set(cur)

    for i in cur_ids - prev_ids:
        events.append({"detected_at": when, "type": "ADDED", "id": i,
                       "name": cur[i]["name"], "field": "team", "old": "", "new": cur[i].get("team", "")})
    for i in prev_ids - cur_ids:
        events.append({"detected_at": when, "type": "REMOVED", "id": i,
                       "name": prev[i]["name"], "field": "team", "old": prev[i].get("team", ""), "new": ""})
    for i in cur_ids & prev_ids:
        a, b = prev[i], cur[i]
        for fld in TRACK_FIELDS:
            if fld not in a:          # field added to the schema later; don't flag it as a change
                continue
            av, bv = (a.get(fld) or ""), (b.get(fld) or "")
            if av != bv:
                events.append({"detected_at": when, "type": "CHANGED", "id": i,
                               "name": b["name"], "field": fld, "old": av, "new": bv})
    return events


def append_timeline(events):
    new = not os.path.exists(TIMELINE_CSV)
    with open(TIMELINE_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["detected_at", "type", "id", "name", "field", "old", "new"])
        for e in events:
            w.writerow([e["detected_at"], e["type"], e["id"], e["name"],
                        e["field"], e["old"], e["new"]])
    with open(TIMELINE_JSONL, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- formatting
def _fmt(e):
    if e["type"] == "ADDED":
        return f"\U0001F7E2 ADDED  {e['name']}  ({e['new'] or 'no team'})"
    if e["type"] == "REMOVED":
        return f"\U0001F534 REMOVED  {e['name']}  ({e['old'] or 'no team'})"
    return f"\u270F\uFE0F {e['name']}  {e['field']}: {e['old'] or '\u2014'} \u2192 {e['new'] or '\u2014'}"


def _plain(e):
    if e["type"] == "ADDED":
        return f"+ ADDED  {e['name']} ({e['new'] or 'no team'})"
    if e["type"] == "REMOVED":
        return f"- REMOVED  {e['name']} ({e['old'] or 'no team'})"
    return f"* {e['name']}  {e['field']}: {e['old'] or '-'} -> {e['new'] or '-'}"


_ORDER = {"ADDED": 0, "CHANGED": 1, "REMOVED": 2}


# ---------------------------------------------------------------- notify
def notify(events, when):
    url = os.environ.get("NBA_WEBHOOK") or WEBHOOK_URL
    if not url or not events:
        return
    import urllib.request
    ev = sorted(events, key=lambda x: (_ORDER.get(x["type"], 9), x["name"]))
    text = "\n".join([f"**nba.com/players changes** \u2014 {when}"] + [_fmt(e) for e in ev])
    if len(text) > 1900:
        text = text[:1900] + f"\n\u2026(+{len(events)} total)"
    key = "text" if "slack.com" in url else "content"
    req = urllib.request.Request(url, data=json.dumps({key: text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"[notify] webhook failed: {e}", file=sys.stderr, flush=True)


def notify_desktop(events, when):
    if not DESKTOP_NOTIFY or not events:
        return
    try:
        from winotify import Notification
    except ImportError:
        print("[desktop] Windows notifications need: pip install winotify", file=sys.stderr, flush=True)
        return
    n = len(events)
    ev = sorted(events, key=lambda x: (_ORDER.get(x["type"], 9), x["name"]))
    lines = [_plain(e) for e in ev[:6]]
    if n > 6:
        lines.append(f"...and {n - 6} more")
    try:
        Notification(app_id="NBA Players Tracker",
                     title=f"NBA: {n} table change{'s' if n != 1 else ''}",
                     msg="\n".join(lines), duration="long").show()
    except Exception as e:  # noqa: BLE001
        print(f"[desktop] toast failed: {e}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- poll / watch
def poll_once(source=None):
    os.makedirs(SNAP_DIR, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = fetch_players(source)
    if not cur:
        print(f"[{now}] parsed 0 players - aborting (not overwriting state).", file=sys.stderr)
        return 0
    prev = load_state()
    if prev is None:
        save_state(cur)
        print(f"[{now}] Baseline saved: {len(cur)} players. Changes tracked from here.")
        return 0
    events = diff_events(prev, cur, now)
    if events:
        append_timeline(events)
        for e in sorted(events, key=lambda x: (_ORDER.get(x["type"], 9), x["name"])):
            print(f"[{now}] {_fmt(e)}", flush=True)
        save_state(cur)
        notify(events, now)
        notify_desktop(events, now)
    else:
        print(f"[{now}] checked - {len(cur)} players, no changes", file=sys.stderr, flush=True)
    return len(events)


def watch(interval, source=None):
    print(f"Watching nba.com/players every {interval}s. Ctrl-C to stop.", flush=True)
    while True:
        try:
            poll_once(source)
        except KeyboardInterrupt:
            print("\nStopped."); return
        except Exception as e:  # noqa: BLE001
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] poll error (will retry): {e}", file=sys.stderr, flush=True)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped."); return


def send_test():
    """Fire a sample notification through the real notify paths (toast + webhook)."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sample = [
        {"detected_at": now, "type": "ADDED", "id": "0", "name": "Test Player",
         "field": "team", "old": "", "new": "BOS"},
        {"detected_at": now, "type": "CHANGED", "id": "0", "name": "Test Player",
         "field": "team", "old": "BOS", "new": "LAL"},
        {"detected_at": now, "type": "REMOVED", "id": "0", "name": "Test Player",
         "field": "team", "old": "LAL", "new": ""},
    ]
    print(f"[{now}] Sending a test notification...")
    for e in sorted(sample, key=lambda x: (_ORDER.get(x["type"], 9), x["name"])):
        print(f"  {_fmt(e)}")

    if os.environ.get("NBA_WEBHOOK") or WEBHOOK_URL:
        notify(sample, now)
        print("  webhook  -> sent (check Discord/Slack)")
    else:
        print("  webhook  -> skipped (no WEBHOOK_URL / NBA_WEBHOOK set)")

    if DESKTOP_NOTIFY:
        notify_desktop(sample, now)
        print("  toast    -> sent (look bottom-right / Action Center)")
    else:
        print("  toast    -> disabled (DESKTOP_NOTIFY = False)")


def main():
    ap = argparse.ArgumentParser(description="Track nba.com/players table changes to the minute.")
    ap.add_argument("source", nargs="?", help="optional saved .html file to parse instead of fetching")
    ap.add_argument("--watch", action="store_true", help="run continuously")
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls (watch)")
    ap.add_argument("--test", action="store_true", help="send a sample notification and exit")
    args = ap.parse_args()
    if args.test:
        send_test()
        return
    if args.watch:
        watch(max(15, args.interval), args.source)
    else:
        poll_once(args.source)


if __name__ == "__main__":
    main()