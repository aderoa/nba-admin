#!/usr/bin/env python3
"""
nba_tracker_all.py  -  Run ALL NBA trackers together (players table + transactions + Google Docs).

Drives the two existing trackers in one go, so you only schedule ONE task:
  * nba_auto_tracker.py          -> the nba.com/players table
  * nba_transactions_tracker.py  -> nba.com/players/transactions (new transactions)
  * nba_docs_tracker.py           -> three Google Docs (mobilebasic)
  * nba_sheets_tracker.py         -> Google Sheets tabs (CSV export)
  * nba_gleague_tracker.py        -> NBA G League transactions

Each keeps its own snapshot folder, state, log, and notifications - this just calls them
back to back. If one feed is down, the other still runs.

USAGE
-----
    python nba_tracker_all.py                 # one check of both (for Task Scheduler)
    python nba_tracker_all.py --watch         # poll both every 60s
    python nba_tracker_all.py --watch --interval 30
    python nba_tracker_all.py --test          # sample notification from both

Schedule the plain command every minute with pythonw.exe (no window), Start in: C:\\Scripts.
Both files must sit in the same folder as this one.
"""

import os
import sys
import time
import argparse
import datetime

# make sure the two tracker modules (same folder) are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import nba_auto_tracker as players
except Exception as e:  # noqa: BLE001
    players = None
    print(f"[setup] could not import nba_auto_tracker.py: {e}", file=sys.stderr)

try:
    import nba_transactions_tracker as transactions
except Exception as e:  # noqa: BLE001
    transactions = None
    print(f"[setup] could not import nba_transactions_tracker.py: {e}", file=sys.stderr)

try:
    import nba_docs_tracker as docs
except Exception as e:  # noqa: BLE001
    docs = None
    print(f"[setup] could not import nba_docs_tracker.py: {e}", file=sys.stderr)

try:
    import nba_sheets_tracker as sheets
except Exception as e:  # noqa: BLE001
    sheets = None
    print(f"[setup] could not import nba_sheets_tracker.py: {e}", file=sys.stderr)

try:
    import nba_gleague_tracker as gleague
except Exception as e:  # noqa: BLE001
    gleague = None
    print(f"[setup] could not import nba_gleague_tracker.py: {e}", file=sys.stderr)

TRACKERS = [("players", players), ("transactions", transactions), ("docs", docs), ("sheets", sheets), ("gleague", gleague)]


def _ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_once():
    for name, mod in TRACKERS:
        if mod is None:
            continue
        try:
            mod.poll_once()
        except Exception as e:  # noqa: BLE001 - one tracker failing must not stop the other
            print(f"[{_ts()}] {name} error (will retry next run): {e}", file=sys.stderr, flush=True)


def watch(interval):
    print(f"Watching players + transactions every {interval}s. Ctrl-C to stop.", flush=True)
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\nStopped."); return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopped."); return


def test_all():
    for name, mod in TRACKERS:
        if mod is None:
            continue
        print(f"--- {name} ---")
        try:
            mod.send_test()
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] test failed: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Run both NBA trackers together.")
    ap.add_argument("--watch", action="store_true", help="run continuously")
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls (watch)")
    ap.add_argument("--test", action="store_true", help="send a sample notification from both")
    args = ap.parse_args()
    if args.test:
        test_all()
    elif args.watch:
        watch(max(15, args.interval))
    else:
        run_once()


if __name__ == "__main__":
    main()