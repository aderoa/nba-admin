#!/usr/bin/env python3
"""
nba_transactions_tracker.py  -  Watch nba.com/players/transactions for NEW transactions.

Unlike the players page, the transactions page doesn't embed its data in the HTML - it
pulls from NBA's public player-movement feed. We read that feed, keep the most recent
TRANSACTIONS_LIMIT rows (100 by default - plenty), and report any transaction we haven't
seen before. It's an append-only log, so the only real "change" is a NEW row; things
aging out of the window are ignored.

USAGE
-----
    python nba_transactions_tracker.py                  # fetch, report new transactions
    python nba_transactions_tracker.py --watch          # poll every 60s
    python nba_transactions_tracker.py --watch --interval 30
    python nba_transactions_tracker.py --test           # send a sample notification
    python nba_transactions_tracker.py feed.json        # parse a saved feed (for testing)
    python nba_transactions_tracker.py --dump           # print the raw feed shape + a sample

Schedule the plain command every minute via Task Scheduler (use pythonw.exe for no window).

OUTPUT (in ./nba_tx_snapshots)   -- its own folder
------------------------------
  state.json               - the latest window of transactions (keyed by a stable hash)
  transactions_log.csv     - append-only: detected_at, date, type, description
  transactions_log.jsonl   - same, one JSON object per line

NOTIFICATIONS: pip install winotify (toast) and/or set WEBHOOK_URL / env NBA_WEBHOOK.
"""

import os
import sys
import csv
import json
import time
import argparse
import datetime

# NBA's public player-movement feed (what the transactions page is built on).
FEED_URL = "https://stats.nba.com/js/data/playermovement/NBA_Player_Movement.json"
TRANSACTIONS_LIMIT = 100     # track only the most recent N transactions
MAX_AGE_DAYS = 30            # ignore transactions whose own date is older than this
WEBHOOK_URL = ""             # Discord/Slack webhook, or env var NBA_WEBHOOK
DESKTOP_NOTIFY = True        # Windows toast (needs: pip install winotify)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Referer": "https://www.nba.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

SNAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_tx_snapshots")
STATE_FILE = os.path.join(SNAP_DIR, "state.json")
SEEN_FILE = os.path.join(SNAP_DIR, "seen.json")  # permanent record of every logged transaction
LOG_CSV = os.path.join(SNAP_DIR, "transactions_log.csv")
LOG_JSONL = os.path.join(SNAP_DIR, "transactions_log.jsonl")


# ---------------------------------------------------------------- fetch/parse
def _find_rows(obj):
    """Locate the transactions list regardless of exact wrapper key."""
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, dict) and isinstance(v.get("rows"), list):
                return v["rows"]
        for v in obj.values():
            r = _find_rows(v)
            if r:
                return r
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return obj
    return None


def _gf(row, *names):
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, ""):
            return v
    return ""


def _row_to_tx(row):
    date = str(_gf(row, "TRANSACTION_DATE", "DATE"))[:10]
    ttype = str(_gf(row, "TRANSACTION_TYPE", "GROUP_SORT"))
    desc = str(_gf(row, "TRANSACTION_DESCRIPTION", "DESCRIPTION"))
    player = str(_gf(row, "PLAYER_SLUG", "PLAYER_ID"))
    team = str(_gf(row, "TEAM_SLUG", "TEAM_ID"))
    if not desc:
        desc = f"{ttype}: {player} ({team})".strip()
    key = f"{date}|{player}|{ttype}|{team}|{desc}"[:400]
    return key, {"date": date, "type": ttype, "player": player, "team": team, "desc": desc}


def _extract(text_or_obj):
    obj = text_or_obj if isinstance(text_or_obj, (dict, list)) else json.loads(text_or_obj)
    rows = _find_rows(obj)
    if rows is None:
        raise RuntimeError("Could not find a transactions 'rows' list in the feed.")
    rows = sorted(rows, key=lambda r: str(_gf(r, "TRANSACTION_DATE", "DATE")), reverse=True)
    out = {}
    for r in rows[:TRANSACTIONS_LIMIT]:
        k, tx = _row_to_tx(r)
        out[k] = tx
    return out


def fetch_transactions(source=None, retries=3):
    if source:
        with open(source, encoding="utf-8", errors="replace") as f:
            return _extract(f.read())
    import urllib.request
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(FEED_URL, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _extract(resp.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * attempt)
    raise RuntimeError(f"Could not fetch/parse the transactions feed after {retries} tries: {last}")


# ---------------------------------------------------------------- state
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


def _fingerprint(tx):
    """Stable id for a transaction: date + description (survives feed flicker; matches the log)."""
    return f"{tx['date']}|{tx['desc']}"


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return None
    return None


def save_seen(seen):
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False)
    os.replace(tmp, SEEN_FILE)


def _seed_from_log():
    """Reconstruct fingerprints from transactions_log.csv so existing entries never re-log."""
    seen = set()
    if os.path.exists(LOG_CSV):
        try:
            with open(LOG_CSV, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    d = (row.get("date") or "")[:10]
                    desc = row.get("description") or ""
                    if desc:
                        seen.add(f"{d}|{desc}")
        except Exception:
            pass
    return seen


def append_log(newtx):
    new = not os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["detected_at", "date", "type", "description"])
        for e in newtx:
            w.writerow([e["detected_at"], e["date"], e["type"], e["desc"]])
    with open(LOG_JSONL, "a", encoding="utf-8") as f:
        for e in newtx:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- notify
def _fmt(e):
    return f"\U0001F4C4 {e['date']}  {e['desc']}"


def notify(newtx, when):
    url = os.environ.get("NBA_WEBHOOK") or WEBHOOK_URL
    if not url or not newtx:
        return
    import urllib.request
    text = "\n".join([f"**New NBA transactions** \u2014 {when}"] + [_fmt(e) for e in newtx])
    if len(text) > 1900:
        text = text[:1900] + f"\n\u2026(+{len(newtx)} total)"
    key = "text" if "slack.com" in url else "content"
    req = urllib.request.Request(url, data=json.dumps({key: text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # noqa: BLE001
        print(f"[notify] webhook failed: {e}", file=sys.stderr, flush=True)


def notify_desktop(newtx, when):
    if not DESKTOP_NOTIFY or not newtx:
        return
    try:
        from winotify import Notification
    except ImportError:
        print("[desktop] Windows notifications need: pip install winotify", file=sys.stderr, flush=True)
        return
    n = len(newtx)
    lines = [f"{e['date']} {e['desc']}" for e in newtx[:5]]
    if n > 5:
        lines.append(f"...and {n - 5} more")
    try:
        Notification(app_id="NBA Transactions Tracker",
                     title=f"NBA: {n} new transaction{'s' if n != 1 else ''}",
                     msg="\n".join(lines), duration="long").show()
    except Exception as e:  # noqa: BLE001
        print(f"[desktop] toast failed: {e}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- poll
def poll_once(source=None):
    os.makedirs(SNAP_DIR, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = fetch_transactions(source)
    if not cur:
        print(f"[{now}] parsed 0 transactions - aborting (not overwriting state).", file=sys.stderr)
        return 0

    cutoff = (datetime.date.today() - datetime.timedelta(days=MAX_AGE_DAYS)).isoformat()
    seen = load_seen()

    # First run (or missing/corrupt seen file): seed from the existing log + current feed,
    # so nothing already known gets re-logged. New ones are tracked from here.
    if not seen:
        seen = _seed_from_log()
        for tx in cur.values():
            seen.add(_fingerprint(tx))
        save_seen(seen)
        print(f"[{now}] Baseline: {len(seen)} known transactions (log + feed). New ones tracked from here.")
        return 0

    newtx = []
    for tx in cur.values():
        fp = _fingerprint(tx)
        if fp in seen:
            continue                                   # already logged before -> no flicker dupes
        if tx["date"] and tx["date"] < cutoff:
            continue                                   # older than MAX_AGE_DAYS -> skip late-posted old moves
        seen.add(fp)
        newtx.append(dict(tx, detected_at=now))

    if newtx:
        newtx.sort(key=lambda e: e["date"], reverse=True)
        append_log(newtx)
        for e in newtx:
            print(f"[{now}] {_fmt(e)}", flush=True)
        save_seen(seen)
        notify(newtx, now)
        notify_desktop(newtx, now)
    else:
        print(f"[{now}] checked - no new transactions", file=sys.stderr, flush=True)
    return len(newtx)


def watch(interval, source=None):
    print(f"Watching NBA transactions every {interval}s. Ctrl-C to stop.", flush=True)
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
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sample = [{"detected_at": now, "date": now[:10], "type": "Signing",
               "desc": "TEST: The Boston Celtics signed free agent Test Player."}]
    print(f"[{now}] Sending a test notification...")
    print(f"  {_fmt(sample[0])}")
    if os.environ.get("NBA_WEBHOOK") or WEBHOOK_URL:
        notify(sample, now); print("  webhook -> sent")
    else:
        print("  webhook -> skipped (no WEBHOOK_URL / NBA_WEBHOOK)")
    if DESKTOP_NOTIFY:
        notify_desktop(sample, now); print("  toast   -> sent")
    else:
        print("  toast   -> disabled")


def dump(source=None):
    """Print the raw feed shape + a sample row, to confirm/adjust field names."""
    import urllib.request
    if source:
        raw = open(source, encoding="utf-8", errors="replace").read()
    else:
        req = urllib.request.Request(FEED_URL, headers=HEADERS)
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    obj = json.loads(raw)
    rows = _find_rows(obj)
    print("top-level keys:", list(obj.keys()) if isinstance(obj, dict) else type(obj))
    print("rows found:", len(rows) if rows else 0)
    if rows:
        print("row keys:", list(rows[0].keys()))
        print("sample row:", json.dumps(rows[0], ensure_ascii=False)[:400])


def main():
    ap = argparse.ArgumentParser(description="Track new NBA transactions to the minute.")
    ap.add_argument("source", nargs="?", help="optional saved feed .json to parse")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--test", action="store_true", help="send a sample notification and exit")
    ap.add_argument("--dump", action="store_true", help="print raw feed shape + a sample row")
    args = ap.parse_args()
    if args.test:
        send_test()
    elif args.dump:
        dump(args.source)
    elif args.watch:
        watch(max(15, args.interval), args.source)
    else:
        poll_once(args.source)


if __name__ == "__main__":
    main()
