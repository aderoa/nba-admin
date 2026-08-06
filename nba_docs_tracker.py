#!/usr/bin/env python3
"""
nba_docs_tracker.py  -  Track Google Docs (mobilebasic view) and log line-level changes.

Fetches each doc's lightweight /mobilebasic HTML, extracts the text, and diffs it against the
previous snapshot. New/removed lines are appended to nba_doc_snapshots/docs_log.csv, which
nba_changelog.py folds into the same report as the players + transactions feeds.

First run per doc just saves a baseline silently (no changes logged), same as the other trackers.

USAGE
-----
    python nba_docs_tracker.py            # one pass; logs changes since last run
    python nba_docs_tracker.py --test     # show what it sees, don't toast
    python nba_docs_tracker.py --silent   # no Windows toast

Schedule it every minute (Task Scheduler with pythonw), or call it from nba_tracker_all.py.
The docs must be viewable by "anyone with the link" for the fetch to work without login.
"""

import os
import re
import csv
import sys
import html
import json
import datetime
import difflib
import urllib.request

try:
    from winotify import Notification, audio
    HAVE_TOAST = True
except Exception:
    HAVE_TOAST = False

BASE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(BASE, "nba_doc_snapshots")
os.makedirs(SNAP, exist_ok=True)
LOG = os.path.join(SNAP, "docs_log.csv")

# The three docs to watch. Optionally give a friendly name; leave "" to use the doc's title.
DOCS = [
    ("", "https://docs.google.com/document/d/1rHEjMqMLJmMf5i40TN_B8pDEkZy2FMtcmNDMk1OqoD8/mobilebasic"),
    ("", "https://docs.google.com/document/d/1xgdR77bKCDz3dEczaeBkuBNaGz17cGH3MKWmX6p4PRs/mobilebasic"),
    ("", "https://docs.google.com/document/d/1z2yFymSI6vJDyIcyP8X4Y_GIUCySJIupIbwa-g-Ar2M/mobilebasic"),
]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (nba-docs-tracker)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def doc_id(url):
    m = re.search(r"/document/d/([^/]+)", url)
    return m.group(1) if m else re.sub(r"[^A-Za-z0-9]+", "_", url)[:40]


def extract(page):
    """Return (title, [lines]) from a mobilebasic HTML page."""
    mt = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    title = html.unescape(mt.group(1)).strip() if mt else ""
    title = re.sub(r"\s*-\s*Google Docs\s*$", "", title).strip()

    body = re.search(r"(?is)<body[^>]*>(.*?)</body>", page)
    body = body.group(1) if body else page
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n", body)
    body = re.sub(r"(?i)<br\s*/?>", "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = html.unescape(body)
    lines = []
    for ln in body.split("\n"):
        ln = re.sub(r"[ \t\u00a0\u200b]+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return title, lines


def load_prev(did):
    p = os.path.join(SNAP, did + ".json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None


def save_snap(did, title, lines):
    json.dump({"title": title, "lines": lines},
              open(os.path.join(SNAP, did + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False)


def diff_lines(old, new):
    """Return list of (type, text): ADDED / REMOVED, ignoring pure reorders."""
    out = []
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            for ln in old[i1:i2]:
                out.append(("REMOVED", ln))
        if tag in ("replace", "insert"):
            for ln in new[j1:j2]:
                out.append(("ADDED", ln))
    return out


def append_log(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["detected_at", "doc", "type", "text"])
        for r in rows:
            w.writerow(r)


def poll_once(silent=False, dry=False):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_changes = []
    per_doc = []

    for friendly, url in DOCS:
        did = doc_id(url)
        try:
            title, lines = extract(fetch(url))
        except Exception as e:
            print(f"fetch failed [{did}]: {e}")
            continue
        label = friendly or title or did
        prev = load_prev(did)
        save_snap(did, title, lines)
        if prev is None:
            print(f"baseline saved: {label} ({len(lines)} lines)")
            continue
        changes = diff_lines(prev.get("lines", []), lines)
        if dry:
            print(f"[{label}] {len(lines)} lines, {len(changes)} change(s)")
        if changes:
            per_doc.append((label, len(changes)))
            for typ, text in changes:
                all_changes.append((now, label, typ, text))

    if all_changes and not dry:
        append_log(all_changes)

    if all_changes:
        summary = ", ".join(f"{name} ({n})" for name, n in per_doc)
        print(f"{len(all_changes)} change(s): {summary}")
        if HAVE_TOAST and not dry and not silent:
            try:
                t = Notification(app_id="NBA Docs Tracker",
                                 title=f"Doc update: {len(all_changes)} change(s)",
                                 msg=summary[:250])
                t.set_audio(audio.Default, loop=False)
                t.show()
            except Exception:
                pass
    else:
        print("no changes" if not dry else "(test) no changes vs last snapshot")


def send_test():
    """Sample notification (used by nba_tracker_all.py --test)."""
    if HAVE_TOAST:
        try:
            t = Notification(app_id="NBA Docs Tracker", title="NBA Docs Tracker",
                             msg="Docs tracking is wired up and working.")
            t.set_audio(audio.Default, loop=False)
            t.show()
            print("sent sample toast")
        except Exception as e:
            print(f"toast failed: {e}")
    else:
        print("winotify not installed (pip install winotify) - no toast sent")


def main():
    poll_once(silent="--silent" in sys.argv, dry="--test" in sys.argv)


if __name__ == "__main__":
    main()
