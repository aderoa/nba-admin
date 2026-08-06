#!/usr/bin/env python3
"""
nba_push_github.py  -  Publish the tracker logs to GitHub so the admin
dashboard can read them from anywhere.

The trackers write CSVs into C:\\Scripts\\nba_*_snapshots. A browser page
cannot read those, and pointing the dashboard at http://localhost only works
on this machine (and is blocked outright from an https page). So this pushes
one JSON file to the nba-admin repo, and the dashboard reads it from
raw.githubusercontent like every other source.

No git, no CLI, no GitHub Desktop: it PUTs through the Contents API with
urllib. Reuses nba_changelog.py's own readers, so the JSON always matches
what the local report shows -- there is no second parser to drift.

    data/events.json      every logged event + a per-feed summary

WHY IT USUALLY DOES NOTHING
  Run every minute from Task Scheduler and a naive version would commit 1,440
  times a day. This computes the git blob SHA of the new content and compares
  it to the SHA already on the branch: identical content is not pushed at all,
  so the repo's commit history is a real record of when something changed --
  which is exactly what makes "last commit" a usable freshness signal on the
  dashboard.

TOKEN
  Needs a fine-grained PAT with Contents: Read and write on the target repo
  only. Looked for in this order:
      env  NBA_ADMIN_TOKEN
      file C:\\Scripts\\.nba_admin_token   (first line)
  Keep it out of any repo. C:\\Scripts is not a working copy, so a file there
  cannot be committed by accident -- which is why that is the fallback rather
  than somewhere alongside the dashboard.

USAGE
    python nba_push_github.py               # push if changed
    python nba_push_github.py --force       # push even if identical
    python nba_push_github.py --dry         # build and report, push nothing
    pythonw nba_push_github.py              # silent, for Task Scheduler

    python nba_push_github.py --out data/events.json
        Write the file instead of pushing it. This is the mode the GitHub
        Actions workflow uses: the runner is already inside a checkout, so it
        commits with GITHUB_TOKEN and no PAT is involved at all. Same builder
        either way, so the file is identical however it got there.
"""

import os
import sys
import json
import base64
import hashlib
import datetime
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "v0.1.0"
OWNER = "aderoa"
REPO = "nba-admin"
BRANCH = "main"
PATH = "data/events.json"
MAX_EVENTS = 1500

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "push_github.log")
TOKEN_FILE = os.path.join(BASE, ".nba_admin_token")

API = "https://api.github.com"


def log(*a):
    """
    pythonw has no console, so a silent failure would be invisible. Same
    pattern as nba_changelog_server.py.
    """
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       " ".join(str(x) for x in a))
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def token():
    t = (os.environ.get("NBA_ADMIN_TOKEN") or "").strip()
    if t:
        return t
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                return f.readline().strip()
        except OSError:
            pass
    return ""


def api(method, path, body=None, tok=""):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "nba-push-github")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=30) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw else {})


def blob_sha(data: bytes) -> str:
    """
    Git's own object id for this content: sha1("blob <len>\\0" + bytes). The
    Contents API returns exactly this as the file's `sha`, so comparing the two
    tells us whether a push would change anything WITHOUT downloading the file.
    """
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def build():
    """The same events the local report shows, via nba_changelog's readers."""
    import nba_changelog as CL
    events = CL.collect_all() if hasattr(CL, "collect_all") else \
        (CL.read_players() + CL.read_tx())
    events.sort(key=lambda e: e["detected_at"], reverse=True)
    feeds = {}
    for e in events:
        f = feeds.setdefault(e["source"], {"count": 0, "last": ""})
        f["count"] += 1
        if e["detected_at"] > f["last"]:
            f["last"] = e["detected_at"]
    payload = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "total": len(events),
        "feeds": feeds,
        "events": events[:MAX_EVENTS],
    }
    # sort_keys so identical data serialises identically -- otherwise dict
    # ordering alone could produce a "change" and a pointless commit.
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      indent=1).encode("utf-8")


def arg(name):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main():
    force = "--force" in sys.argv
    dry = "--dry" in sys.argv
    out = arg("--out")

    try:
        content = build()
    except Exception as e:
        log("build failed:", e)
        return 1
    payload = json.loads(content)
    log(f"built {len(content)} bytes, {payload['total']} events, "
        f"{len(payload['feeds'])} feed(s)")
    for k, v in sorted(payload["feeds"].items()):
        log(f"   {k:<14} {v['count']:>5}  newest {v['last']}")

    if dry:
        log("--dry: nothing pushed")
        return 0

    if out:
        # Inside a checkout (Actions, or a local clone): write and let git do
        # the committing. No token, no API call, no network.
        d = os.path.dirname(os.path.abspath(out))
        if d:
            os.makedirs(d, exist_ok=True)
        before = None
        if os.path.exists(out):
            with open(out, "rb") as f:
                before = f.read()
        if before == content and not force:
            log(f"unchanged -- {out} left alone")
            return 0
        with open(out, "wb") as f:
            f.write(content)
        log(f"wrote {out} ({len(content)} bytes)")
        return 0

    tok = token()
    if not tok:
        log("NO TOKEN. Set NBA_ADMIN_TOKEN, or put a fine-grained PAT "
            f"(Contents: read+write on {OWNER}/{REPO}) in {TOKEN_FILE}")
        return 1

    # current file, if any
    sha = None
    try:
        st, cur = api("GET", f"/repos/{OWNER}/{REPO}/contents/{PATH}"
                             f"?ref={BRANCH}", tok=tok)
        sha = cur.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log("first push -- file does not exist yet")
        elif e.code == 401:
            log("token rejected (401) -- check it has not expired")
            return 1
        elif e.code == 403:
            log("403 -- token lacks Contents write on this repo, or rate limited")
            return 1
        else:
            log(f"GET failed: HTTP {e.code} {e.reason}")
            return 1
    except Exception as e:
        log("GET failed:", e)
        return 1

    if sha and sha == blob_sha(content) and not force:
        log("unchanged -- not pushing (--force to push anyway)")
        return 0

    body = {
        "message": f"tracker events: {payload['total']} logged "
                   f"({datetime.datetime.now():%Y-%m-%d %H:%M})",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    try:
        st, res = api("PUT", f"/repos/{OWNER}/{REPO}/contents/{PATH}",
                      body, tok=tok)
        c = (res.get("commit") or {}).get("sha", "")[:7]
        log(f"pushed -> {OWNER}/{REPO}/{PATH}  commit {c}")
        return 0
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("message", "")
        except Exception:
            pass
        if e.code == 409:
            # someone else wrote between our GET and PUT; next run will win
            log("409 conflict -- the file moved under us, retrying next run")
            return 0
        log(f"PUT failed: HTTP {e.code} {e.reason} {detail}")
        return 1
    except Exception as e:
        log("PUT failed:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
