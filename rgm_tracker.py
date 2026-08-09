#!/usr/bin/env python3
"""
rgm_tracker.py  -  Player movement from RealGM's international box scores.

An appearance is proof of a signing, and RealGM indexes every international
game by DATE -- so one page lists the day's fixtures and there is no id space
to scan, no ceiling to bisect, no block structure to exploit. It covers the
leagues the other sources could not reach: Mexican LNBP, Canadian CEBL, the
PBA, Korea, Argentina.

    python rgm_tracker.py --days 7 --baseline   # FIRST RUN: learn, log nothing
    python rgm_tracker.py --days 3 --section wnba
    python rgm_tracker.py --days 3 --section all      # international + wnba
    python rgm_tracker.py --date 2026-08-06
    python rgm_tracker.py --days 3            # today and the two before
    python rgm_tracker.py --forget-signed     # drop baseline SIGNED rows
    python rgm_tracker.py --report
    python rgm_tracker.py --results 2026-08-06   # final scores from the state
    python rgm_tracker.py --rescore              # forget games, keep players
    python rgm_tracker.py --poll               # what Task Scheduler runs
    python rgm_tracker.py --date 2026-08-06 --leagues 76,54   # only these

WHAT MAKES THIS SOURCE THE RIGHT ONE
  Two things that every other attempt lacked.

  A STABLE PLAYER ID. /player/{slug}/Summary/{id} -- so a player is the same
  person across leagues and seasons. FIBA LiveStats had no player id at all and
  had to be keyed on a hash pulled out of a photo url.

  THE LEAGUE, STATED. Team links read
      /international/league/76/Mexican-LNBP/team/2091/Astros-de-Jalisco
  giving league id, league name and team id together. The FIBA feed named no
  competition whatsoever, which forced a guess from team names -- and that
  guess mislabelled Belarusian youth sides as Philippine pro games.

STRUCTURE, AS PROBED against Astros de Jalisco at Abejas (526071)
  * the day's index carries each game three times over; ids must be deduped
  * box score tables are the ones whose headers include Status and Min
  * 18 columns, not the 16 a header slice suggested:
      #, Player, Status, Pos, Min, FGM-A, 3PM-A, FTM-A, FIC,
      Off, Def, Reb, Ast, PF, STL, TO, BLK, PTS
    Cells are read BY HEADER NAME rather than by position, so a column being
    added or moved cannot silently shift every stat by one.
  * Status says "Starter" outright, so nothing has to be inferred from minutes
  * /preview/ links are scheduled games with no data -- skipped, and the date
    is left unfinished so it is revisited

RUN --baseline FIRST
  Every player's first appearance is a SIGNED event, so an initial run over
  three days produced 253 of them -- which is not 253 signings, it is the
  rosters of three leagues being written down. They would also crowd every
  other feed out of the dashboard payload, which keeps only the newest 1,500
  events across all sources.

  --baseline records state and logs nothing. After that a SIGNED event means a
  player genuinely new to the data -- an import arriving in the LNBP -- which
  IS news.

DRIVEN BY nba_tracker_all
  poll_once() and send_test() are exposed, so it sits alongside the other
  trackers. It keeps its own 60-minute floor: a day is a dozen requests at two
  seconds each, and games finish on the hour, not the minute.

  Each run also revisits any date that still had SCHEDULED games. Tonight's PBA
  fixtures are /preview/ links with no box score, so without that the day would
  be closed off and the games never read.

POLITENESS
  robots.txt sets crawl-delay: 2, honoured here as in the transaction scraper.
  A day is a dozen requests, so a week of catch-up is a couple of minutes.
"""

import os
import re
import csv
import sys
import json
import time
import datetime

VERSION = "v0.2.0"
BASE = "https://basketball.realgm.com"
CRAWL_DELAY = 2.0
MIN_INTERVAL_MIN = 60          # games end on the hour, not the minute
POLL_DAYS = 2                  # today and yesterday, plus unfinished dates

try:
    from winotify import Notification, audio
    HAVE_TOAST = True
except Exception:
    HAVE_TOAST = False

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "nba_rgm_snapshots")
os.makedirs(SNAP, exist_ok=True)
STATE = os.path.join(SNAP, "rgm_state.json")
LOG = os.path.join(SNAP, "rgm_log.csv")

# Box-score lines, written for whatever rates them later.
#
# This tracker already fetches every box score and parses every line, then keeps
# only the movement. Writing the lines out as well costs one file append and
# saves a second pass over the same pages -- and it means a league that appears
# for the first time is picked up automatically, because the tracker reads the
# whole day rather than filtering to leagues it already knows.
#
# RAW FIGURES ONLY. No rating is computed here and none may be: this file lives
# in a public repo, and the formula must not be importable from it.
GR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_rgm_gr")
GR_COLS = ["game_id", "date", "section", "league", "phase", "team", "opp", "won",
           "player", "player_id", "player_url", "min", "pts", "reb", "ast",
           "stl", "blk", "tov", "pf", "fgm", "fga", "ftm", "fta", "oreb",
           "poss", "url"]
# Shared with rgm_gr.py: one record of what entered the rating cache, whichever
# side put it there. The viewer reads it to say which games are new.
ADDED_COLS = ["added_at", "source", "section", "league", "phase", "date",
              "game_id", "teams", "score", "lines", "url"]

# Site-wide and identical in every section, which is what makes one tracker
# possible: DeWanna Bonner is 3000014 and A.J. Slaughter is 10169, in the same
# id space, so a player is the same person wherever he or she turns up.
PLAYER_RE = re.compile(r"/player/([^/]+)/Summary/(\d+)", re.I)

# The sections differ in three ways, all of them here rather than scattered
# through the code:
#
#   index url     /international/scores/DATE/All   but   /wnba/scores/DATE
#   team hrefs    /international/league/{lid}/{League}/team/{tid}/{Slug}
#                 /wnba/teams/{Slug}/{tid}/Home      -- no league segment
#   the league    named in the international href; simply "WNBA" otherwise
#
# Column ORDER also differs -- international ends STL, TO, BLK, PTS and the
# WNBA ends STL, BLK, TO, PTS -- which costs nothing here because cells are
# read by header name. Positionally it would have swapped blocks and turnovers.
SECTIONS = {
    "international": {
        "index_suffix": "/All",
        "movement": True,
        "team_re": re.compile(
            r"/international/league/(\d+)/([^/]+)/team/(\d+)/([^/?#]+)", re.I),
        "league": None,          # taken from the href
    },
    "wnba": {
        "index_suffix": "",
        "team_re": re.compile(r"/wnba/teams/([^/]+)/(\d+)/", re.I),
        "league": "WNBA",
        "movement": True,
    },
    "national": {
        "index_suffix": "",
        # The href shape here is unconfirmed, and it does not have to be: when
        # no team link matches, the team name is taken from the url slug
        # ("Spain-at-France"), which every section provides. A better regex only
        # improves the label.
        "team_re": re.compile(r"/national/(?:team|nation|teams)/([^/]+)/(\d+)", re.I),
        # Read from the page, not fixed. The fallback below only applies when a
        # box score has no competition line at all.
        "league": "National Teams",
        # MOVEMENT IS OFF FOR NATIONAL GAMES, and this is the whole reason the
        # flag exists. A player turning out for Spain has not left his club: fed
        # to the movement logic, every EuroBasket squad would log as a transfer
        # to "Spain" and then a transfer back a fortnight later. Scores and
        # ratings are unaffected -- those are properties of the game, not of who
        # employs anyone.
        "movement": False,
    },
}


def game_re(section):
    return re.compile(r"/" + re.escape(section) +
                      r"/(boxscore|preview)/(\d{4}-\d{2}-\d{2})/([^/]+)/(\d+)",
                      re.I)


def get(url, first=False):
    try:
        from curl_cffi import requests as cr
    except ImportError:
        print("needs curl_cffi:  pip install curl_cffi")
        return None
    if not first:
        time.sleep(CRAWL_DELAY)
    for imp in ("chrome124", "chrome120", "chrome110"):
        try:
            r = cr.get(url, impersonate=imp, timeout=45,
                       headers={"Referer": BASE + "/international/scores"})
            if r.status_code == 200 and len(r.text) > 3000:
                return r.text
        except Exception:                                # noqa: BLE001
            pass
    print(f"  !! could not fetch {url}")
    return None


def soup_of(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                st = json.load(f)
            return migrate(st)
        except (OSError, ValueError):
            pass
    return {"players": {}, "games": {}, "dates": {}}


def migrate(st):
    """
    Bring old date keys into the sectioned form.

    Dates used to be keyed by date alone; adding the WNBA made that ambiguous,
    so they are now "section|date". A leftover bare key still resolves -- it
    defaults to international -- but it also shows up forever in the list of
    unfinished dates, next to the sectioned key for the same day. Two entries
    for one thing reads like a fault, so the old form is folded in and dropped.
    """
    dates = st.get("dates") or {}
    for k in [k for k in dates if "|" not in k]:
        v = dates.pop(k)
        nk = f"{v.get('section', 'international')}|{k}"
        v.setdefault("section", "international")
        v.setdefault("date", k)
        # Keep whichever is less complete: an unfinished day must stay
        # unfinished, or its games are never read.
        old = dates.get(nk)
        if old is None or (old.get("complete") and not v.get("complete")):
            dates[nk] = v
    st["dates"] = dates
    return st


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _n(v, d=0):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return d


def write_gr_lines(section, date, gid, slug, league, phase, teams, url):
    """
    Append this game's lines to the rating cache.

    Possessions are computed here because they come from the box score and
    nothing else -- FGA, FTA, TOV and OREB, preferring the totals row so team
    rebounds and team turnovers are not lost. That is arithmetic on published
    figures, not a rating, and the distinction is the whole reason this can live
    in a public file.
    """
    if len(teams) != 2:
        return 0
    try:
        os.makedirs(GR_DIR, exist_ok=True)
    except OSError:
        return 0

    def totals(t):
        tot = t.get("totals") or {}
        def pair(v):
            m = re.match(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$", str(v or ""))
            return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        _, fga = pair(tot.get("fgm-a"))
        _, fta = pair(tot.get("ftm-a"))
        d = {"fga": fga, "fta": fta, "tov": _n(tot.get("to")),
             "oreb": _n(tot.get("off")), "pts": _n(tot.get("pts"))}
        if not d["fga"]:
            for p in t.get("players", []):
                d["fga"] += _n(p.get("fga")); d["fta"] += _n(p.get("fta"))
                d["tov"] += _n(p.get("tov")); d["oreb"] += _n(p.get("oreb"))
                d["pts"] += _n(p.get("pts"))
        return d

    a, b = totals(teams[0]), totals(teams[1])
    poss = ((a["fga"] + b["fga"]) - (a["oreb"] + b["oreb"])
            + (a["tov"] + b["tov"]) + 0.44 * (a["fta"] + b["fta"])) / 2.0
    if poss <= 0:
        return 0
    sc = [teams[0].get("score"), teams[1].get("score")]
    if None in sc:
        sc = [a["pts"], b["pts"]]

    path = os.path.join(GR_DIR, f"lines_{section}_{date[:4]}.csv")
    # Never write a game twice: the historical backfill writes to this same
    # file, and the two must not double-count a day they both saw.
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                if any(r.get("game_id") == str(gid) for r in csv.DictReader(f)):
                    return 0
        except OSError:
            pass
    rows = []
    for i, t in enumerate(teams):
        won = (sc[0] > sc[1]) if i == 0 else (sc[1] > sc[0])
        for p in t.get("players", []):
            mins = p.get("min", "0")
            m = re.match(r"^(\d+):(\d+)$", str(mins).strip())
            mv = (int(m.group(1)) + int(m.group(2)) / 60.0) if m else _n(mins)
            if mv <= 0:
                continue
            rows.append({"game_id": gid, "date": date, "section": section,
                         "league": league, "phase": phase, "team": t["team"],
                         "opp": teams[1 - i]["team"], "won": int(won),
                         "player": p.get("name", ""), "player_id": p.get("id", ""),
                         "player_url": (f"{BASE}/player/{p['slug']}/Summary/{p['id']}"
                                        if p.get("slug") and p.get("id") else ""),
                         "min": round(mv, 2), "pts": _n(p.get("pts")),
                         "reb": _n(p.get("reb")), "ast": _n(p.get("ast")),
                         "stl": _n(p.get("stl")), "blk": _n(p.get("blk")),
                         "tov": _n(p.get("tov")), "pf": _n(p.get("pf")),
                         "fgm": _n(p.get("fgm")), "fga": _n(p.get("fga")),
                         "ftm": _n(p.get("ftm")), "fta": _n(p.get("fta")),
                         "oreb": _n(p.get("oreb")), "poss": round(poss, 3),
                         "url": url})
    if not rows:
        return 0
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=GR_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)
    try:
        ap = os.path.join(GR_DIR, "added.csv")
        anew = not os.path.exists(ap)
        with open(ap, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ADDED_COLS, extrasaction="ignore")
            if anew:
                w.writeheader()
            w.writerow({"added_at": now(), "source": "tracker",
                        "section": section, "league": league, "phase": phase,
                        "date": date, "game_id": gid,
                        "teams": f"{teams[0]['team']} v {teams[1]['team']}",
                        "score": f"{sc[0]}-{sc[1]}", "lines": len(rows),
                        "url": url})
    except OSError:
        pass
    return len(rows)


def append_log(rows):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["detected_at", "game_date", "league", "league_id",
                        "player", "player_id", "type", "team", "from_team",
                        "game_id", "min", "pts", "starter", "url"])
        w.writerows(rows)


# ---------------------------------------------------------------- index

def day_games(date, section="international"):
    """
    -> (played, scheduled). Each a list of (game_id, slug).

    Both sections list every game three times over, so ids are deduped while
    keeping first-seen order.
    """
    cfg = SECTIONS[section]
    html = get(f"{BASE}/{section}/scores/{date}{cfg['index_suffix']}", first=True)
    if not html:
        return [], []
    rx = game_re(section)
    played, sched, seen = [], [], set()
    for a in soup_of(html).find_all("a", href=True):
        m = rx.search(a["href"])
        if not m:
            continue
        kind, _d, slug, gid = m.groups()
        if gid in seen:
            continue
        seen.add(gid)
        (played if kind.lower() == "boxscore" else sched).append((gid, slug))
    return played, sched


# ---------------------------------------------------------------- box score

PHASES = [
    # POSTSEASON FIRST, then preseason, and only then regular season.
    #
    # Order is the whole trick. Almost every page mentions "Regular Season"
    # somewhere -- a standings link, a nav item -- so testing for it first
    # labelled the Puerto Rican and Lebanese finals as regular-season games.
    # A page that says "Finals" or "Semifinals" is a postseason game whatever
    # else it also says, so those are checked before the general case.
    ("Championship Series", "playoffs"),
    # NOT bare "Championship": it is in the NAME of half the competitions in
    # world basketball -- AmeriCup Championship, Asian Championship -- and
    # matching it labelled a batch of February 2025 World Cup qualifiers as
    # playoff games. A phase word has to describe a stage, not a tournament.
    ("Quarterfinals", "playoffs"), ("Quarter-Finals", "playoffs"),
    ("Semifinals", "playoffs"), ("Semi-Finals", "playoffs"),
    ("Playoffs", "playoffs"), ("Playoff", "playoffs"),
    ("Postseason", "playoffs"), ("Post-Season", "playoffs"),
    ("Finals", "playoffs"), ("Grand Final", "playoffs"),
    # "Final" as well as "Finals": the optional plural is appended to the label,
    # so "Finals" + "s?" cannot match the singular. An Olympic final says Final.
    # Safe this late in the list -- Quarterfinals and Semifinals matched already.
    ("Final", "playoffs"),
    ("Play-In", "playin"), ("Play In", "playin"),
    ("Preseason", "preseason"), ("Pre-Season", "preseason"),
    ("Exhibition", "exhibition"), ("Friendly", "exhibition"),
    ("Qualifier", "qualifier"), ("Group Stage", "group"),
    ("Regular Season", "regular"),
]


# The competition line sits between the date and "Attendance:" on a box score:
#
#   Ireland 54, Switzerland 85
#   February 20, 2025
#   European World Cup Pre-Qualifier - Pool Play    Attendance: N/A
#
# National games need this because their team links carry no league, and one
# label of "National Teams" for a EuroBasket, an AmeriCup and a World Cup
# pre-qualifier is no label at all.
COMP_RE = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December)\s+\d{1,2},\s+\d{4}\s+(.{3,90}?)\s+Attendance",
    re.I | re.S)


def competition(html):
    """
    -> (competition, stage). Either may be "".

    "European World Cup Pre-Qualifier - Pool Play" splits into the competition
    and the stage: the first is what groups a table, the second is a phase. They
    are separated because grouping by the full string would give a different
    "league" for pool play and for the knockout rounds of the same tournament.
    """
    if not html:
        return "", ""
    # The WHOLE page, not the header. On a national box score the competition
    # line sits BELOW the line-score table -- cutting at the first <table> was
    # excluding the one thing this function exists to find, and every game came
    # back labelled with the section default. The pattern is anchored on a date
    # followed by "Attendance", which is specific enough not to need a window.
    txt = " ".join(re.sub(r"<[^>]+>", " ", html[:60000]).split())
    m = COMP_RE.search(txt)
    if not m:
        return "", ""
    full = " ".join(m.group(1).split())
    # An en dash or a hyphen, either spaced or not.
    parts = re.split(r"\s+[-\u2013\u2014]\s+", full, maxsplit=1)
    comp = parts[0].strip(" -\u2013\u2014")
    stage = parts[1].strip() if len(parts) > 1 else ""
    return comp, stage


def game_phase(html):
    """
    Regular season, preseason, playoffs -- as RealGM labels it on the page.

    Needed because a date range cannot tell them apart: the 2026 WNBA regular
    season began on 8 May, and the games before it are preseason sitting in the
    same index. Rating those alongside the real thing quietly pads everyone's
    season. The label is in a table header on the box score, so it is read
    rather than inferred from the calendar.
    """
    if not html:
        return ""
    # Only the game's own header area, not the whole page. Site navigation
    # mentions every phase there is, so scanning further finds words that have
    # nothing to do with this fixture. The header sits above the box score
    # tables, so everything before the first <table> is the honest window.
    cut = html.lower().find("<table")
    # Always stop at the first table when there is one, however early it comes.
    # A "cut > 400" guard meant a short header fell back to scanning the whole
    # document, which reintroduced the very problem the cut exists to prevent:
    # finding "Playoffs" in a standings table and calling a regular-season game
    # a postseason one.
    head = html[:cut] if cut > 0 else html[:20000]
    txt = " ".join(re.sub(r"<[^>]+>", " ", head).split())
    # The stage half of the competition line is the most specific thing on the
    # page -- "Pool Play", "Quarterfinals" -- so it is tested before the rest of
    # the header, where a tournament's name can look like a stage.
    _c, stage = competition(html)
    if stage:
        for label, key in PHASES:
            if re.search(r"\b" + re.escape(label) + r"s?\b", stage, re.I):
                return key
        # "Group C" and "Pool B" are group stages as much as "Group Stage" is --
        # matching only the literal phrase left every group game unlabelled.
        if re.search(r"\bgroups?\b|\bpool\b|\bround robin\b", stage, re.I):
            return "group"
    # Then the competition NAME. "FIBA World Cup Qualifiers - Second Round" has
    # a stage that says nothing, and "Friendly" has no stage at all -- in both
    # the name is the answer. It is checked after the stage because a stage is
    # the more specific of the two: an AmeriCup quarterfinal is a playoff game,
    # not a qualifier, even though the tournament is a qualifying event.
    if _c:
        for label, key in PHASES:
            if re.search(r"\b" + re.escape(label) + r"s?\b", _c, re.I):
                return key
    for label, key in PHASES:
        # An optional plural, because pages say "Qualifiers" and the list says
        # "Qualifier" -- and \b after the singular is blocked by the trailing s,
        # so a whole tournament's worth of qualifying games came back unlabelled.
        if re.search(r"\b" + re.escape(label) + r"s?\b", txt, re.I):
            return key
    return ""


def phase_words(html):
    """Every phase-ish phrase on a page, for working out how a league labels
    its postseason when game_phase comes back empty."""
    if not html:
        return []
    txt = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    out = []
    for label, _k in PHASES:
        n = len(re.findall(r"\b" + re.escape(label) + r"s?\b", txt, re.I))
        if n:
            i = txt.lower().find(label.lower())
            out.append((label, n, txt[max(0, i - 50):i + 50]))
    return out


def parse_box(html, slug, section="international"):
    """
    -> (league_id, league_name, [ {team, team_id, score, periods, players} ... ])

    The team is read from its OWN link rather than from table order: the url
    slug gives "Away-at-Home", and each team link ends in a matching slug, so
    the pairing is confirmed instead of assumed. The page also carries a large
    navigation menu full of unrelated team links -- Maccabi, Cibona, Zalgiris --
    which is exactly why matching against the slug matters.
    """
    cfg = SECTIONS[section]
    s = soup_of(html)
    parts = re.split(r"-at-", slug, maxsplit=1)
    want = [p.lower() for p in parts] if len(parts) == 2 else []
    # The slug carries the proper case -- "Spain-at-France" -- and it is the only
    # source of a team name when a section has no team links. Lowercasing for
    # matching is fine; lowercasing the LABEL gave a board reading "spain".
    want_raw = [p.replace("-", " ") for p in parts] if len(parts) == 2 else []

    def fold(x):
        return re.sub(r"[^a-z0-9]", "", (x or "").lower())

    league_id = ""
    league_name = cfg["league"] or ""
    # A competition named on the page always beats the section's default:
    # "FIBA World Cup Qualifiers" rather than "National Teams" for every game
    # ever played by a country.
    comp, _stage = competition(html)
    if comp and cfg.get("league"):
        league_name = comp
    by_slug = {}
    for a in s.find_all("a", href=True):
        m = cfg["team_re"].search(a["href"])
        if not m:
            continue
        text = a.get_text(strip=True)
        if section == "wnba":
            tslug, tid = m.group(1), m.group(2)
            lid, lname = "", "WNBA"
            # The WNBA page lists all thirteen clubs in its nav, so the two
            # playing teams are found by matching the LINK TEXT to the url
            # slug -- "Phoenix-at-Atlanta" against links reading "Phoenix" and
            # "Atlanta". Matching the href slug would fail on "LA-Sparks",
            # whose href says Los-Angeles-Sparks.
            keys = {fold(text), fold(tslug)}
        else:
            lid, lname, tid, tslug = m.groups()
            # "PBA--Governors-Cup" -> "PBA Governors Cup": a double hyphen
            # otherwise leaves a double space in every log line.
            lname = " ".join(lname.replace("-", " ").split())
            keys = {fold(tslug)}
        full = tslug.replace("-", " ")
        for k in keys:
            if k and k not in by_slug:
                by_slug[k] = (lid, lname, tid, full)
        if any(fold(w) in keys for w in want) and not league_id:
            league_id, league_name = lid, lname

    # The line score is the table whose headers are period numbers ending in
    # Final -- ['', '1', '2', '3', '4', 'Final'] -- and it is the only place the
    # result appears. It has no player links, which is why it was skipped
    # before. Stored on the game record rather than logged as an event: a
    # finished game is not player movement, and fifteen results a day across
    # eight leagues would bury the movement feed.
    line = []
    for t in s.find_all("table"):
        heads = [" ".join(th.get_text(" ", strip=True).split())
                 for th in t.find_all("th")]
        if len(heads) >= 3 and heads[-1].lower() == "final" \
                and any(h.isdigit() for h in heads):
            for tr in t.find_all("tr"):
                cells = [" ".join(td.get_text(" ", strip=True).split())
                         for td in tr.find_all(["td", "th"])]
                if not cells:
                    continue
                name = (cells[0] or "").strip()
                nums = [c for c in cells if re.fullmatch(r"-?\d+", c or "")]
                # A team's row is a NAME followed by a number per period and
                # the total. The header row -- ['', '1','2','3','4','Final'] --
                # passed the old test: its first cell is empty, which is not a
                # digit, and 1,2,3,4 look like quarter scores. Meralco was duly
                # recorded as having scored 4. So the name must be present and
                # non-numeric, and the total must equal the periods.
                if not name or name.isdigit() or len(nums) < 3:
                    continue
                per, final = [int(x) for x in nums[:-1]], int(nums[-1])
                if sum(per) != final:
                    continue        # a header, or a totals row -- not a team
                line.append({"team": name, "periods": per, "final": final})
            break

    # box score tables: the ones with a Status column and player links
    tables = []
    for t in s.find_all("table"):
        heads = [" ".join(th.get_text(" ", strip=True).split())
                 for th in t.find_all("th")]
        hl = [h.lower() for h in heads]
        if "status" in hl and "min" in hl and t.find("a", href=PLAYER_RE):
            tables.append((heads, t))

    teams = []
    for i, (heads, t) in enumerate(tables[:2]):
        # away first, home second -- but only used to pick WHICH slug, and the
        # slug is what names the team
        tslug = want[i] if i < len(want) else ""
        info = by_slug.get(re.sub(r"[^a-z0-9]", "", tslug))
        team_name = info[3] if info else (
            (want_raw[i] if i < len(want_raw) else "") or f"team{i+1}")
        team_id = info[2] if info else ""
        idx = {h.lower(): j for j, h in enumerate(heads)}
        players = []
        for a in t.find_all("a", href=PLAYER_RE):
            tr = a.find_parent("tr")
            if tr is None:
                continue
            cells = [" ".join(td.get_text(" ", strip=True).split())
                     for td in tr.find_all(["td", "th"])]
            if not cells:
                continue

            def cell(name, default=""):
                # BY NAME, never by position: the row has 18 columns and a
                # header slice of 16 was enough to make PTS look like TO.
                j = idx.get(name)
                return cells[j] if j is not None and j < len(cells) else default
            _pm = PLAYER_RE.search(a["href"])
            pid = _pm.group(2)
            # The slug as well as the id, because RealGM's player page needs
            # both: /player/Xavier-Moon/Summary/89303. The id alone identifies
            # him; the pair is what links to him.
            pslug = _pm.group(1)
            # The full line, not just minutes and points. These are RealGM's
            # own box-score figures and nothing derived -- no rating is computed
            # here, deliberately: this file lives in a PUBLIC repo and the
            # rating formula must never be importable from it. rgm_rating.py
            # does that, locally.
            def pair(name):
                # "12-25" -> (12, 25); RealGM writes makes-attempts in one cell
                v = cell(name)
                m = re.match(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$", v or "")
                return (m.group(1), m.group(2)) if m else ("", "")
            fgm, fga = pair("fgm-a")
            tpm, tpa = pair("3pm-a")
            ftm, fta = pair("ftm-a")
            players.append({
                "id": pid, "slug": pslug,
                "name": a.get_text(strip=True),
                "status": cell("status"),
                "pos": cell("pos"),
                "min": cell("min"),
                "pts": cell("pts") or (cells[-1] if cells else ""),
                "fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa,
                "ftm": ftm, "fta": fta,
                "oreb": cell("off"), "dreb": cell("def"), "reb": cell("reb"),
                "ast": cell("ast"), "stl": cell("stl"), "blk": cell("blk"),
                "tov": cell("to"), "pf": cell("pf"),
            })
        # Match the line score to the team by name where possible; fall back
        # to row order, which is away-then-home on every page seen. Getting it
        # backwards would record a win as a loss, so a mismatch stores nothing
        # rather than a guess.
        sc = per = None
        if len(line) >= 2:
            cand = None
            for L in line:
                if fold(L["team"]) and fold(L["team"]) == fold(team_name):
                    cand = L
                    break
            if cand is None and i < len(line):
                cand = line[i]
            if cand is not None:
                sc, per = cand["final"], cand["periods"]
        # The TOTALS row, which is not a player row and so has no player link.
        # It matters because team rebounds and team turnovers belong to nobody:
        # on a real PBA box score the players sum to 14 turnovers and 9
        # offensive boards while the totals say 16 and 10. Both feed the
        # possession count, so summing the players would quietly undercount it.
        totals = {}
        for tr in t.find_all("tr"):
            if tr.find("a", href=PLAYER_RE):
                continue                      # a player line, not the total
            cells = [" ".join(td.get_text(" ", strip=True).split())
                     for td in tr.find_all(["td", "th"])]
            if len(cells) < len(heads) - 2:
                continue
            vals = {}
            for name, j in idx.items():
                if j < len(cells):
                    vals[name] = cells[j]
            # The totals row is the one carrying real minutes and points; the
            # "Team" row above it is nearly all blanks.
            def n(x):
                try:
                    return float(str(x).replace(",", ""))
                except (TypeError, ValueError):
                    return None
            if n(vals.get("min")) and n(vals.get("pts")):
                totals = vals
        teams.append({"team": team_name, "team_id": team_id, "score": sc,
                      "periods": per, "players": players, "totals": totals})
    return league_id, league_name, teams


# ---------------------------------------------------------------- ingest

def ingest(dates, only_leagues=None, baseline=False, section="international"):
    state = load_state()
    players, games, done_dates = state["players"], state["games"], state["dates"]
    rows, n_new, n_games, n_base = [], 0, 0, 0
    n_lines = 0
    # A section that does not track movement cannot flood anything, so the
    # warning would just be noise.
    if not players and not baseline and SECTIONS[section].get("movement", True):
        print("\n  !! state is empty, so EVERY player will log as SIGNED -- the")
        print("     rosters of whole leagues, not signings. Stop, run with")
        print("     --baseline first, then drop the flag.\n")

    for date in dates:
        played, sched = day_games(date, section)
        print(f"\n{date} [{section}]: {len(played)} played, "
              f"{len(sched)} scheduled"
              + ("" if SECTIONS[section].get("movement", True)
                 else "   (movement off: national teams)"))
        if not played and not sched:
            print("  nothing listed")
            continue
        for gid, slug in played:
            if gid in games:
                continue
            box_url = f"{BASE}/{section}/boxscore/{date}/{slug}/{gid}"
            html = get(box_url)
            if not html:
                continue
            lid, lname, teams = parse_box(html, slug, section)
            if only_leagues and lid not in only_leagues:
                games[gid] = {"date": date, "league": lname, "slug": slug, "section": section, "skipped": 1}
                continue
            if not teams:
                print(f"  {gid}  {slug}: no box score tables found")
                games[gid] = {"date": date, "league": lname, "slug": slug, "section": section, "empty": 1}
                continue
            n_games += 1
            track_movement = SECTIONS[section].get("movement", True)
            tn = " / ".join(t["team"] for t in teams)
            sc = [t.get("score") for t in teams]
            res = f"  {sc[0]}-{sc[1]}" if len(sc) == 2 and None not in sc else ""
            print(f"  {gid}  {lname or '?'}: {tn}{res}")
            for t in teams:
                if not track_movement:
                    continue          # see SECTIONS[...]["movement"]
                for p in t["players"]:
                    key = p["id"]
                    prev = players.get(key)
                    if prev is None:
                        players[key] = {"name": p["name"], "team": t["team"],
                                        "team_id": t["team_id"], "league": lname,
                                        "history": [t["team"]], "first_seen": now()}
                        if baseline:
                            n_base += 1
                        else:
                            rows.append([now(), date, lname, lid, p["name"], key,
                                         "SIGNED", t["team"], "", gid, p["min"],
                                         p["pts"], p["status"], box_url])
                            n_new += 1
                    elif prev.get("team") != t["team"]:
                        typ = "RETURN" if t["team"] in (prev.get("history") or []) \
                            else "MOVED"
                        rows.append([now(), date, lname, lid, p["name"], key, typ,
                                     t["team"], prev.get("team", ""), gid,
                                     p["min"], p["pts"], p["status"], box_url])
                        hist = prev.get("history") or []
                        if t["team"] not in hist:
                            hist.append(t["team"])
                        players[key] = {"name": p["name"], "team": t["team"],
                                        "team_id": t["team_id"], "league": lname,
                                        "history": hist,
                                        "first_seen": prev.get("first_seen", now())}
                        n_new += 1
                    else:
                        prev["name"] = p["name"]
            # slug and section are stored so the box score url can be rebuilt
            # later without re-deriving it -- rgm_rating.py needs to revisit
            # these pages, and guessing the section would fetch the wrong one.
            n_lines += write_gr_lines(section, date, gid, slug, lname,
                                      game_phase(html), teams, box_url)
            games[gid] = {"date": date, "league": lname, "teams": tn,
                          "slug": slug, "section": section,
                          "score": [t.get("score") for t in teams],
                          "periods": [t.get("periods") for t in teams],
                          "url": box_url, "read_at": now()}
            save_state(state)
        # A date with scheduled games is NOT finished -- those box scores appear
        # after tip-off, so the date must be revisited rather than marked done.
        # keyed by section too, or the WNBA's day would overwrite the
        # international day's completeness and games would be lost
        done_dates[f"{section}|{date}"] = {
            "at": now(), "played": len(played), "scheduled": len(sched),
            "complete": 0 if sched else 1, "section": section, "date": date}
        save_state(state)

    if rows:
        append_log(rows)
    if n_lines:
        print(f"  {n_lines} box-score line(s) cached for rating")
    print(f"\n{n_games} new game(s), {n_new} movement event(s)"
          + (f", {n_base} player(s) baselined (not logged)" if baseline else ""))
    for r in rows[:25]:
        frm = f"  (from {r[8]})" if r[8] else ""
        print(f"   {r[2][:18]:<20} {r[6]:<7} {r[4][:24]:<26} -> {r[7]}{frm}")
    incomplete = [d for d, v in done_dates.items() if not v.get("complete")]
    if incomplete:
        print(f"\n{len(incomplete)} date(s) still had scheduled games -- re-run "
              f"them later: {', '.join(sorted(incomplete)[-5:])}")
    print(f"state: {len(players)} players, {len(games)} games")


def forget_signed():
    """
    Drop SIGNED rows, keep MOVED and RETURN.

    For a log written before --baseline existed. State is untouched, so those
    players stay known and a future club change still reports.
    """
    if not os.path.exists(LOG):
        print("no log yet")
        return
    with open(LOG, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        print("empty log")
        return
    head, body = rows[0], rows[1:]
    if "type" not in head:
        print("no type column")
        return
    ti = head.index("type")
    keep = [r for r in body if len(r) > ti and r[ti] != "SIGNED"]
    dropped = len(body) - len(keep)
    if not dropped:
        print(f"no SIGNED rows to drop ({len(body)} kept)")
        return
    os.replace(LOG, LOG + ".bak")
    with open(LOG, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(keep)
    print(f"dropped {dropped} SIGNED row(s), kept {len(keep)} real move(s)")
    print(f"  previous log saved as {LOG}.bak")


def poll_once(silent=False, force=False):
    """
    One scheduled run: recent dates plus any left unfinished.

    Called every minute by nba_tracker_all and mostly returns at once -- the
    floor is what stops a dozen two-second requests running on top of the
    previous batch.
    """
    state = load_state()
    last = (state.get("meta") or {}).get("polled_at", "")
    if last and not force:
        try:
            age = datetime.datetime.now() - datetime.datetime.strptime(
                last, "%Y-%m-%d %H:%M:%S")
            if age.total_seconds() < MIN_INTERVAL_MIN * 60:
                print(f"skipped -- last poll {int(age.total_seconds()//60)}m ago "
                      f"(runs every {MIN_INTERVAL_MIN}m; --force to override)")
                return
        except ValueError:
            pass

    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=i)).isoformat()
             for i in range(POLL_DAYS)]
    # Unfinished dates come back: a /preview/ link means the box score does not
    # exist yet, and closing the date would lose the game entirely.
    todo = {}
    for sec in SECTIONS:
        todo[sec] = list(dates)
    for k, v in (state.get("dates") or {}).items():
        if v.get("complete"):
            continue
        sec, d = v.get("section", "international"), v.get("date") or k
        if sec in todo and d not in todo[sec]:
            todo[sec].append(d)

    before = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    for sec, ds in todo.items():
        ingest(sorted(set(ds), reverse=True)[:10], section=sec)
    state = load_state()
    state.setdefault("meta", {})["polled_at"] = now()
    save_state(state)

    if HAVE_TOAST and not silent and os.path.exists(LOG) \
            and os.path.getsize(LOG) > before:
        try:
            with open(LOG, encoding="utf-8") as f:
                new = list(csv.DictReader(f))[-4:]
            n = Notification(app_id="RealGM Movement",
                             title="International movement",
                             msg="\n".join(f"{r['player']} -> {r['team']}"
                                           for r in new)[:250])
            n.set_audio(audio.Default, loop=False)
            n.show()
        except Exception:                                # noqa: BLE001
            pass


def send_test():
    if HAVE_TOAST:
        try:
            n = Notification(app_id="RealGM Movement", title="RealGM tracker",
                             msg="International box score tracking is wired up.")
            n.set_audio(audio.Default, loop=False)
            n.show()
            print("sent sample toast")
        except Exception as e:                           # noqa: BLE001
            print(f"toast failed: {e}")
    else:
        print("winotify not installed - no toast sent")


def rescore():
    """
    Forget which games have been read, keep every player.

    Games are read once and never again, so a parser fix does not reach the
    records already stored -- two games sat with a score of 4 taken from the
    line score's HEADER row long after the parser stopped doing that. Clearing
    games and dates makes the next run re-read them.

    players is deliberately untouched. Wiping it would make every one of the
    817 known players log as a fresh SIGNED on the next pass, turning a repair
    into a flood.
    """
    state = load_state()
    ng, nd = len(state.get("games") or {}), len(state.get("dates") or {})
    state["games"] = {}
    state["dates"] = {}
    save_state(state)
    print(f"forgot {ng} game(s) and {nd} date(s); "
          f"{len(state.get('players') or {})} player(s) kept")
    print("\nNow re-read them -- --baseline so nothing logs as a new signing:")
    print("   python rgm_tracker.py --days 14 --section all --baseline")
    print("   python rgm_tracker.py --results")


def results(date=None, n=40):
    """
    Final scores already collected, from the state file -- no fetching.

    A by-product of reading the box scores: the line score is on the same page,
    so eight leagues' results accumulate for free. Each game is read ONCE and
    then skipped forever, so these are always final, never live.
    """
    state = load_state()
    games = [(gid, g) for gid, g in (state.get("games") or {}).items()
             if g.get("score") and None not in (g.get("score") or [None])]
    if date:
        games = [x for x in games if x[1].get("date") == date]
    games.sort(key=lambda x: (x[1].get("date", ""), x[0]), reverse=True)
    print(f"{len(games)} game(s) with a final score"
          + (f" on {date}" if date else ""))
    for gid, g in games[:n]:
        a, b = g["score"]
        teams = (g.get("teams") or " / ").split(" / ")
        aw = teams[0] if teams else "?"
        hm = teams[1] if len(teams) > 1 else "?"
        per = g.get("periods") or []
        qs = ""
        if len(per) == 2 and per[0] and per[1]:
            qs = "   (" + ", ".join(f"{x}-{y}" for x, y in zip(per[0], per[1])) + ")"
        print(f"  {g.get('date','')}  {g.get('league','')[:20]:<22} "
              f"{aw[:22]:<24} {a:>3} - {b:<3} {hm[:22]}{qs}")


def report(n=40):
    if not os.path.exists(LOG):
        print("no log yet")
        return
    with open(LOG, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"{len(rows)} movement event(s)")
    for r in rows[-n:]:
        frm = f"  (from {r['from_team']})" if r["from_team"] else ""
        print(f"  {r['game_date']}  {r['league'][:18]:<20} {r['type']:<7} "
              f"{r['player'][:24]:<26} -> {r['team']}{frm}")


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    if "--report" in sys.argv:
        report()
        return
    if "--forget-signed" in sys.argv:
        forget_signed()
        return
    if "--rescore" in sys.argv:
        rescore()
        return
    if "--results" in sys.argv:
        i = sys.argv.index("--results")
        d = sys.argv[i + 1] if i + 1 < len(sys.argv) and \
            re.match(r"^\d{4}-\d{2}-\d{2}$", sys.argv[i + 1]) else None
        results(d)
        return
    if "--test" in sys.argv:
        send_test()
        return
    if "--poll" in sys.argv:
        poll_once(silent="--silent" in sys.argv, force="--force" in sys.argv)
        return
    dates = []
    d = arg("--date")
    if d:
        dates = [d]
    else:
        n = int(arg("--days", "1"))
        today = datetime.date.today()
        dates = [(today - datetime.timedelta(days=i)).isoformat()
                 for i in range(n)]
    lg = arg("--leagues")
    only = set(x.strip() for x in lg.split(",")) if lg else None
    baseline = "--baseline" in sys.argv
    sec = (arg("--section", "international") or "").lower()
    secs = list(SECTIONS) if sec == "all" else [sec]
    for x in secs:
        if x not in SECTIONS:
            print(f"unknown section {x!r} -- choose from "
                  f"{', '.join(SECTIONS)}, or all")
            return
    print(f"rgm_tracker {VERSION}  dates: {', '.join(dates)}"
          + f"  section(s): {', '.join(secs)}"
          + (f"  leagues: {sorted(only)}" if only else "")
          + ("  BASELINE (recording, not logging)" if baseline else ""))
    for x in secs:
        ingest(dates, only, baseline, x)


if __name__ == "__main__":
    main()
