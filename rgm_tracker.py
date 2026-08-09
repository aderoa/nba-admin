#!/usr/bin/env python3
"""
rgm_gr.py  --  Season Global Rating for WNBA, international leagues and
               national-team tournaments, from RealGM box scores.
==========================================================================
  LOCAL ONLY.  C:\\Scripts.  DO NOT UPLOAD TO aderoa/nba-admin.
==========================================================================
Imports global_rating, so it inherits that file's rules. nba-admin is public and
its workflow requires the trackers committed there, which is why the rating side
lives in files like this one and never in rgm_tracker.py.

The NBA equivalent is gr_history.py (per game) plus gr_season.py (aggregate).
This does both for leagues nba_api has never heard of.

    python rgm_gr.py --backfill --section wnba --from 2026-05-01 --to 2026-09-30
    python rgm_gr.py --season   --section wnba --year 2026
    python rgm_gr.py --season   --section wnba --year 2026 --html
    python rgm_gr.py --backfill --section international --from 2026-03-01 --to 2026-07-24
        Widening the window costs only the games it keeps: teams already in the
        cache identify their leagues from the url slug, so everything else is
        skipped without being opened. --leagues "BSN,CEBL" narrows it further.
    python rgm_gr.py --season   --section national --year 2026 --min-gp 3
    python rgm_gr.py --season   --section wnba --year 2026 --phase playoffs
    python rgm_gr.py --coverage --section international --year 2026 \\
        --from 2026-03-01 --to 2026-08-09
        What is in the index but not in the cache, and would the filter take it
        now? The filter seeds from the cache, so a first pass skips games whose
        teams it had not yet met -- a second pass, with a bigger seed, finds
        them. Add --fetch to collect them.

        --match "Indios" reports only fixtures involving that team, and lists all
        of them rather than the first 25 -- which is what you want when one
        club's game count looks short.

    python rgm_gr.py --season --section national --years all --min-gp 3
        Qualifying cycles are rated across all the years given, labelled
        "2025-26". Everything else -- EuroBasket U17, the Olympics, AmeriCup --
        gets one table per year, because those are separate tournaments.
        nba_rgm_gr/cycles.txt decides which is which.
    python rgm_gr.py --season --section national --years 2025,2026 --min-gp 3
        Rate several calendar years as one competition, labelled "2025-26".
        "all" uses every year cached for the section. Competition NAMES are left
        alone, so qualifying stays separated by continent -- see league_alias.txt
        if you want stages within one continent merged.
        Rate several calendar years as one competition. A World Cup qualifying
        cycle spans two years, and nba_rgm_gr/league_alias.txt groups its stages
        under one name -- so pre-qualifiers and qualifiers from both years become
        a single table instead of four of three games each.

    python rgm_gr.py --urls americup_qualifiers.txt
        Fetch an explicit list of box score urls, one per line. Each url carries
        its own section, date and id, so a list spanning several seasons is
        routed to the right cache automatically -- which is what an AmeriCup
        qualifying cycle running from February 2024 to February 2025 needs.
        Far faster than scanning a year of index pages for two dozen games.

    python rgm_gr.py --auto
        Re-rate only the sections whose cached games are newer than their
        ratings. Cheap when there is nothing to do, so it suits a scheduled task
        every few minutes -- and it is what the viewer's "Rate now" button runs.

    python rgm_gr.py --refetch "National Teams" --dry
    python rgm_gr.py --refetch "National Teams"
        Re-read one competition's games with the current parser. A parser fix
        cannot reach cached games -- the backfill skips ids it has seen -- so a
        better reading of the competition name leaves earlier games labelled the
        old way. Each cached line stores its url, so the games are re-fetched by
        name rather than by scanning indexes.

    python rgm_gr.py --tidy --dry
    python rgm_gr.py --tidy
        One derived file per competition, newest kept. Season labels move as a
        cache grows -- "2025" becomes "2024-25" -- and the older file otherwise
        stays on disk, doubling every player in the viewer.

    python rgm_gr.py --realign
        Rewrite every lines cache so the header and the values agree. Needed once
        after a column was added: appending to a file whose header predates the
        change shifts every new row, and a player url turns up where minutes
        should be.

    python rgm_gr.py --catchup   --section wnba --days 5
        Fetch the last few days and re-rate. Use this when "Last day" sits
        behind: the tracker skips games already in its own state, so games it
        read before it began writing lines never produced any.
    python rgm_gr.py --who "Breanna Stewart" --section wnba --year 2026
    python rgm_gr.py --inspect 160027 --section wnba --year 2026
    python rgm_gr.py --ids --section wnba --year 2026        # id gaps
    python rgm_gr.py --phases --section international --year 2026
        How does each league label its games? Fetches a couple per league and
        prints the phase wording found, so regular season and playoffs can be
        told apart instead of guessed at.
    python rgm_gr.py --season --section wnba --year 2026 --exclude 160027
    python rgm_gr.py --season --section wnba --year 2026 --keep-outliers

  Preseason and exhibition games are dropped by default -- see DROP_PHASES.

TWO PHASES, ON PURPOSE
  Fetching is slow and rating is instant. Backfill caches every box-score line
  to disk once; the season pass then re-reads that cache. So retuning the
  games-played curve, changing the minimum, or switching the aggregation costs
  nothing -- exactly the property gr_season.py documents ("Nothing in this file
  requires re-fetching").

WHICH PART OF THE FORMULA
  game_score x win/loss, with possessions taken from each box score's totals
  row. Not efficiency: that needs net rating, which RealGM does not publish.
  Possessions are NOT a defensive statistic -- they come from FGA, FTA, TOV and
  OREB, all on the page -- so the per-possession normalisation is intact. Per
  the formula file's own note, dropping efficiency costs about one All-NBA slot
  in six hundred across forty-one seasons.

AGGREGATION, MIRRORING gr_season.py
  MEAN_GR   sum(GR) / GP        -- rate, no availability term
  GP_RATIO  GP / team games     -- capped at 1
  GR_LINEAR MEAN_GR x GP_RATIO  -- the reported figure

  Team games are counted from the games actually collected, not assumed from a
  schedule: a partial backfill then understates nobody, because every player is
  measured against the same collected set.

  GP_RATIO is capped at 1 because a traded player can appear in more games than
  either single team played, which would otherwise read as a bonus for changing
  clubs mid-season.

WHAT IT WILL NOT DO
  Pool leagues into one table. A 30 in the PBA and a 30 in the WNBA are not the
  same achievement, and per-possession arithmetic does not make them so. One
  season, one section, one league at a time.
"""

import os
import re
import csv
import sys
import json
import datetime
import glob
import collections
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from global_rating import (GameLine, game_score, wl, parse_minutes,
                               team_possessions, VERSION as GR_VERSION)
except ImportError:
    print("global_rating.py not found beside this script.")
    print("It is C:\\Scripts only and must never be committed to a repo.")
    sys.exit(1)
try:
    import rgm_tracker as RGM
except ImportError:
    print("rgm_tracker.py not found beside this script.")
    sys.exit(1)

VERSION = "v0.33.0-refetch"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_rgm_gr")
WANTED = [None]        # --leagues, a comma-separated substring filter
os.makedirs(OUT, exist_ok=True)

GP_MODE = "linear"        # none | linear | exponent   (see gr_season.py)
GP_EXPONENT = 0.35        # only used by mode "exponent"

LINE_COLS = ["game_id", "date", "section", "league", "phase", "team", "opp", "won",
             "player", "player_id", "player_url", "min", "pts", "reb", "ast",
             "stl", "blk", "tov", "pf", "fgm", "fga", "ftm", "fta", "oreb",
             "poss", "url"]


def num(v, d=0):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return d


def cache_path(section, year):
    return os.path.join(OUT, f"lines_{section}_{year}.csv")


FAILED = os.path.join(OUT, "failed.txt")

# An append-only record of games entering the cache: when, which league, which
# fixture. Without it "something changed" is all a viewer can say -- and the
# useful question is WHICH games moved the numbers, not that they moved.
ADDED = os.path.join(OUT, "added.csv")

# Leagues to keep out, by name fragment.
#
# Pruning rows is not enough on its own. The slug filter seeds from the teams in
# the cache, and a team can play in two competitions: Astros de Jalisco are in
# the Mexican LNBP (wanted) and turned up in a Champions League Americas game
# (not wanted). Delete that row and the next such fixture is fetched again,
# because Astros are still a known team. A standing block is the only thing that
# holds -- so a blocked league is dropped at ingest AND filtered at read time.
#
# One fragment per line, '#' for comments. Fragments, not exact names, because
# RealGM's rendering shifts ("Commissioners Cup" / "Commissioner's Cup").
BLOCKED_FILE = os.path.join(OUT, "blocked_leagues.txt")


def load_blocked(year=None):
    """
    Blocked league fragments that apply to `year`.

    Entries take two forms:

        CIBACOPA              blocked in every season
        2026: CIBACOPA        blocked in 2026 only

    The season-scoped form exists because of a genuine conflict. A competition
    that has finished for the year must be kept OUT of this season's cache --
    otherwise the slug filter, which matches TEAMS and cannot see competitions,
    keeps pulling it back in: Astros de Jalisco play in the Mexican LNBP, so
    their Champions League Americas games pass the filter forever. But blocking
    it permanently would also suppress it when the 2026-27 season starts, which
    is the opposite of what is wanted. Scoping the block to a year gives both.
    """
    out = []
    if not os.path.exists(BLOCKED_FILE):
        return out
    try:
        with open(BLOCKED_FILE, encoding="utf-8") as f:
            for line in f:
                t = line.split("#")[0].strip()
                if not t:
                    continue
                m = re.match(r"^(\d{4})\s*:\s*(.+)$", t)
                if m:
                    if year is None or int(m.group(1)) == int(year):
                        out.append(m.group(2).strip().lower())
                else:
                    out.append(t.lower())
    except OSError:
        pass
    return out


def is_blocked(league, blocked=None):
    lg = (league or "").lower()
    for b in (blocked if blocked is not None else load_blocked()):
        if b in lg:
            return b
    return None


def add_blocked(names, year=None):
    """Write blocks, scoped to a season unless year is None."""
    have = set(load_blocked(year))
    new = [n.strip() for n in names if n.strip() and n.strip().lower() not in have]
    if not new:
        return 0
    fresh = not os.path.exists(BLOCKED_FILE)
    try:
        with open(BLOCKED_FILE, "a", encoding="utf-8") as f:
            if fresh:
                f.write("# Leagues kept out of the ratings, by name fragment.\n"
                        "#   CIBACOPA        every season\n"
                        "#   2026: CIBACOPA  that season only\n"
                        "# Delete a line to let one back in.\n")
            for n in new:
                f.write((f"{year}: {n}" if year else n) + "\n")
    except OSError:
        return 0
    return len(new)
ADDED_COLS = ["added_at", "source", "section", "league", "phase", "date",
              "game_id", "teams", "score", "lines", "url"]


def align_added():
    """
    Bring added.csv up to the current columns.

    The same trap as the lines cache: this file was created before "url" existed,
    so appending wrote eleven values under a ten-name header. url is the last
    column, so nothing shifted -- it was simply dropped, which is why the Added
    tab had no links however many games were logged.
    """
    if not os.path.exists(ADDED):
        return
    try:
        with open(ADDED, encoding="utf-8") as f:
            raw = list(csv.reader(f))
    except OSError:
        return
    if not raw or raw[0] == ADDED_COLS:
        return
    old = raw[0]
    rows = []
    for r in raw[1:]:
        if not r:
            continue
        # By FIELD COUNT, not by the file's header. Rows written after a column
        # was added carry the new width, and zipping those against the old header
        # truncates the very value being recovered.
        layout = ADDED_COLS if len(r) == len(ADDED_COLS) else old
        d = dict(zip(layout, r))
        rows.append({c: d.get(c, "") for c in ADDED_COLS})
    try:
        tmp = ADDED + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ADDED_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, ADDED)
        print(f"  realigned added.csv: {len(rows)} row(s) "
              f"({len(ADDED_COLS)-len(old)} column(s) added)")
    except OSError:
        pass


def note_added(row):
    try:
        align_added()
        new = not os.path.exists(ADDED)
        with open(ADDED, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ADDED_COLS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)
    except OSError:
        pass                     # a missing audit trail must not stop a backfill


def fetch_retry(url, tries=3, wait=6.0):
    """
    RGM.get with backoff, and a record of what still would not come.

    A single pass over three browser impersonations is enough for a normal page
    and not enough for a rate limit -- one WNBA box score failed mid-backfill and
    was simply lost. A few hundred requests will always include a handful of
    these, so they are retried with a widening pause and then written down rather
    than dropped silently.
    """
    for i in range(tries):
        html = RGM.get(url)
        if html:
            return html
        if i + 1 < tries:
            pause = wait * (i + 1)
            print(f"    retry {i+1}/{tries-1} in {pause:.0f}s")
            time.sleep(pause)
    try:
        with open(FAILED, "a", encoding="utf-8") as f:
            f.write(url + "\n")
    except OSError:
        pass
    print(f"    !! gave up: {url}")
    print(f"       recorded in {FAILED} -- rerun the backfill to try again")
    return None


def phases_report(section, year, per=2):
    """
    Fetch a couple of games per league and show what phase wording each page
    carries.

    Needed because "phases in cache: ?=710" says only that nothing was detected,
    not why. Every league labels its postseason differently -- Finals,
    Championship Series, Semifinals, or nothing at all -- and guessing produced
    two wrong answers on the WNBA Cup game already. This reads the pages.
    """
    path = cache_path(section, year)
    if not os.path.exists(path):
        print(f"no cache at {path}")
        return
    by_lg = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            by_lg.setdefault(r["league"], {})[r["game_id"]] = r
    print(f"sampling {per} game(s) per league from {len(by_lg)} league(s)\n")
    for lg, games in by_lg.items():
        # newest first: a postseason game, if there is one, is at the end
        picks = sorted(games.values(), key=lambda r: r["date"], reverse=True)[:per]
        print(f"{lg}")
        for r in picks:
            html = fetch_retry(r["url"], tries=2, wait=4)
            if not html:
                continue
            ph = RGM.game_phase(html)
            print(f"   {r['date']}  {r['team']} v {r['opp']}   detected: {ph or '(none)'}")
            for label, n, ctx in RGM.phase_words(html)[:6]:
                print(f"      {label:<22} x{n:<3} ...{ctx}...")
        print()
    print("If a postseason game shows no wording at all, RealGM does not label it")
    print("and the id-isolation rule or exclude_ids.txt is the only handle.")


def ids_report(section, year, gap=100):
    """
    The season's game ids, and where the gaps are.

    Written because the automatic outlier rule failed and there was no way to see
    why. Ids that sit apart from their neighbours are the candidates for a
    fixture that is not part of the regular season -- the Commissioner's Cup
    final, a neutral-site showcase. Read the list, then name the id in
    exclude_ids.txt.
    """
    path = cache_path(section, year)
    if not os.path.exists(path):
        print(f"no cache at {path}")
        return
    seen = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if str(r["game_id"]).isdigit():
                seen.setdefault(int(r["game_id"]),
                                (r["date"], r["team"], r["opp"], r.get("phase") or "?"))
    ids = sorted(seen)
    print(f"{len(ids)} game(s), ids {ids[0]}..{ids[-1]}")
    print(f"\n  gaps larger than {gap}:")
    shown = 0
    for i in range(1, len(ids)):
        d = ids[i] - ids[i - 1]
        if d > gap:
            a, b = ids[i - 1], ids[i]
            print(f"    {a}  ->  {b}   (+{d})")
            for x in (a, b):
                dt, t, opp, ph = seen[x]
                print(f"        {x}  {dt}  {t} v {opp}  [{ph}]")
            shown += 1
    if not shown:
        print("    none")
    print(f"\n  the highest ids:")
    for x in ids[-8:]:
        dt, t, opp, ph = seen[x]
        print(f"    {x}  {dt}  {t} v {opp}  [{ph}]")
    print(f"\n  To drop one, add its id to {EXCLUDE_FILE}")
    print(f"  (one per line, '#' for comments), or pass --exclude 160027")


def inspect(section, year, gid):
    """
    Fetch one game and show what the page says about itself.

    For the game that does not belong. Breanna Stewart's cache held 32 regular
    season games against RealGM's 31, and one id -- 160027 -- sat 1,400 clear of
    every other game in the season. Guessing at why produced two wrong answers,
    so this prints the phase label, the page title and any header text instead.
    """
    path = cache_path(section, year)
    url = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game_id") == str(gid):
                    url = r.get("url"); break
    if not url:
        print(f"game {gid} not in the cache")
        return
    print(f"  {url}")
    html = fetch_retry(url)
    if not html:
        return
    print(f"\n  phase detected : {RGM.game_phase(html)!r}")
    # Where does a competition NAME live on this page? The national section has
    # no league in its team links, so the label has to come from the header --
    # and "National Teams" for every game is useless.
    cut = html.lower().find("<table")
    head = html[:cut] if cut > 0 else html[:20000]
    print("\n  HEADER, above the first table:")
    for tag in ("title", "h1", "h2", "h3", "h4", "caption"):
        for m in re.finditer(r"<" + tag + r"[^>]*>(.*?)</" + tag + r">", head,
                             re.S | re.I):
            t = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
            if t and len(t) < 120:
                print(f"    <{tag}> {t}")
    crumbs = re.findall(r'<a[^>]+href="(/(?:national|international|wnba)[^"]*)"[^>]*>(.*?)</a>',
                        head, re.S | re.I)
    if crumbs:
        print("\n  section links in the header (a competition may be one):")
        seen = set()
        for href, txt in crumbs[:14]:
            t = " ".join(re.sub(r"<[^>]+>", " ", txt).split())
            if t and (href, t) not in seen:
                seen.add((href, t))
                print(f"    {t[:44]:<46} {href[:70]}")
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if m:
        print(f"  page title     : {' '.join(m.group(1).split())}")
    # every phase-ish word the page contains, and how often
    txt = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    words = ["Regular Season", "Preseason", "Pre-Season", "Playoffs", "Finals",
             "Commissioner", "Cup", "Exhibition", "Friendly", "All-Star",
             "Showcase", "Neutral", "Tournament", "Qualifier"]
    print("\n  phrases on the page:")
    for w in words:
        n = len(re.findall(r"\b" + re.escape(w) + r"\b", txt, re.I))
        if n:
            i = txt.lower().find(w.lower())
            print(f"    {w:<16} x{n:<3} ...{txt[max(0,i-60):i+60]}...")
    # the <th> cells, which is where the WNBA index carries the phase
    ths = re.findall(r"<th[^>]*>(.*?)</th>", html[:30000], re.S | re.I)
    ths = [" ".join(re.sub(r"<[^>]+>", " ", t).split()) for t in ths]
    ths = [t for t in ths if t and len(t) < 60][:16]
    print(f"\n  first table headers: {ths}")


def who(section, year, name):
    """
    Every game held for one player: date, opponent, phase, url.

    Built because the Liberty read one game more than RealGM's own totals and
    two guesses at why were both wrong -- a re-listed fixture, then a team-name
    variant. Neither fired. Printing the actual list settles which game is the
    extra one instead of theorising about it.
    """
    path = cache_path(section, year)
    if not os.path.exists(path):
        print(f"no cache at {path}")
        return
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if name.lower() in (r.get("player") or "").lower()]
    if not rows:
        print(f"no lines for {name!r}")
        return
    rows.sort(key=lambda r: (r.get("date", ""), r.get("game_id", "")))
    print(f"{len(rows)} line(s) for {rows[0]['player']}")
    print(f"  {'#':>3} {'DATE':<12} {'PHASE':<10} {'TEAM':<20} {'OPP':<20} "
          f"{'MIN':>5} {'PTS':>4}  GAME")
    print("  " + "-" * 100)
    for i, r in enumerate(rows, 1):
        print(f"  {i:>3} {r['date']:<12} {(r.get('phase') or '?'):<10} "
              f"{r['team'][:19]:<20} {r['opp'][:19]:<20} "
              f"{float(r['min'] or 0):>5.1f} {r['pts']:>4}  {r['game_id']}")
    dates = [r["date"] for r in rows]
    dup = {d for d in dates if dates.count(d) > 1}
    if dup:
        print(f"\n  repeated date(s): {sorted(dup)}")
    print(f"\n  distinct dates: {len(set(dates))}   phases: "
          + ", ".join(sorted({(r.get('phase') or '?') for r in rows})))
    print("  Compare the count with RealGM's own season total. An extra game")
    print("  usually means a fixture that does not count towards it -- a")
    print("  Commissioner's Cup final, an exhibition, a neutral-site showcase.")


def canon_map(names):
    """
    lowercase name -> the canonical spelling.

    Built from the data rather than a hardcoded list, because every section
    labels teams differently and a list would rot. Where one name is a prefix of
    another, the longer is canonical: "New York" and "New York Liberty" are the
    same club, and the full name is the useful label.
    """
    uniq = {}
    for n in names:
        n = (n or "").strip()
        if n:
            uniq[n.lower()] = n
    out = {}
    for low, full in uniq.items():
        best = full
        for low2, full2 in uniq.items():
            if low2 != low and low2.startswith(low) and len(full2) > len(best):
                best = full2
        out[low] = best
    return out


def fixture_key(date, a, b):
    """
    One fixture, however RealGM numbers it.

    Deduping on game_id alone is not enough: the same match can appear under two
    ids, and then a player picks up an extra game. Breanna Stewart showed 32 when
    the Liberty had played 31. gr_history.py hit the same thing from nba_api and
    dedupes on (PLAYER_ID, GAME_ID) -- the equivalent here is the fixture, since
    two teams meet at most once on a date.
    """
    return (date, ) + tuple(sorted([(a or "").strip().lower(),
                                    (b or "").strip().lower()]))


def dates_between(a, b):
    d0 = datetime.date.fromisoformat(a)
    d1 = datetime.date.fromisoformat(b)
    out = []
    while d0 <= d1:
        out.append(d0.isoformat())
        d0 += datetime.timedelta(days=1)
    return out


# ---------------------------------------------------------------- backfill

def known_teams(section, year, wanted=None):
    """
    folded team name -> league, learned from whatever is already cached.

    This is what makes a wide backfill affordable. The index for a date lists
    every international game in the world and does not say which league any of
    them belongs to -- so going back to March meant opening a thousand box
    scores to keep a hundred. But the URL SLUG carries both team names
    ("Leones-de-Ponce-at-Santeros-de-Aguada"), and the cache already knows which
    teams play in the leagues being tracked. Matching the slug against those
    names decides whether a game is worth fetching without fetching it.
    """
    path = cache_path(section, year)
    out = {}
    if not os.path.exists(path):
        return out
    want = None
    if wanted:
        want = [w.strip().lower() for w in wanted.split(",") if w.strip()]
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lg = r.get("league") or ""
            if want and not any(w in lg.lower() for w in want):
                continue
            for t in (r.get("team"), r.get("opp")):
                k = re.sub(r"[^a-z0-9]", "", (t or "").lower())
                if k:
                    out[k] = lg
    return out


def slug_teams(slug):
    """"A-at-B" -> the two team names, folded for comparison."""
    parts = re.split(r"-at-", slug, maxsplit=1)
    if len(parts) != 2:
        return []
    return [re.sub(r"[^a-z0-9]", "", p.replace("-", " ").lower()) for p in parts]


def slug_wanted(slug, known):
    """
    Is either side a team we track?

    ONE side is enough. A club whose only games fell outside the window already
    cached would otherwise be invisible forever -- matching on either team lets
    coverage grow as the backfill runs, since a game that gets through teaches
    the other team's name.
    """
    if not known:
        return True                      # nothing learned yet: fetch everything
    for t in slug_teams(slug):
        if t in known:
            return True
        # tolerate a slug that abbreviates or extends the stored name
        for k in known:
            if len(k) >= 6 and (k.startswith(t) or t.startswith(k)):
                return True
    return False


def drop_leagues(section, year, names, dry=False, block=False):
    """
    Remove leagues from this season's cache. Blocking is OPT-IN, and usually wrong.

    The inverse of --leagues: name what to get rid of rather than what to keep.
    Matching is on fragments, so "CIBACOPA" removes the Mexican CIBACOPA without
    touching the Mexican LNBP, and "Commissioner" removes the PBA Commissioner's
    Cup while leaving the Governors' Cup alone -- which "PBA" would not.

    NO BLOCK BY DEFAULT. A competition that has simply finished its season needs
    nothing more than its rows removing: no further games will arrive. The block
    file is global while the caches are per-year, so blocking a finished
    competition today would silently suppress it when 2026-27 starts -- the exact
    outcome to avoid. --block is for a league you never want, not one that is
    merely over.

    The derived season_*.csv and lastday_*.csv are removed too, since the viewer
    reads whatever is on disk and would otherwise keep showing a league that is
    no longer in the cache.
    """
    frags = [n.strip() for n in names.split(",") if n.strip()]
    if not frags:
        print('  --drop needs names, e.g. --drop "CIBACOPA,Brazilian NBB"')
        return
    path = cache_path(section, year)
    if not os.path.exists(path):
        print(f"no cache at {path}")
        return
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    low = [f.lower() for f in frags]
    keep, drop = [], []
    for r in rows:
        lg = (r.get("league") or "").lower()
        (drop if any(x in lg for x in low) else keep).append(r)
    hit_lg = sorted({r["league"] for r in drop})
    safe_lg = sorted({r["league"] for r in keep})
    print(f"drop: {len(rows)} line(s) -> {len(keep)} kept, {len(drop)} removed")
    print(f"  removing ({len(hit_lg)}): " + (", ".join(hit_lg) if hit_lg else "nothing matched"))
    # Show what SURVIVED a near-miss, because a fragment that also catches a
    # league you want is the way this goes wrong.
    for f in frags:
        near = [l for l in safe_lg if f.split()[0].lower() in l.lower()]
        if near:
            print(f"  '{f}' left alone: " + ", ".join(near))
    if dry:
        print("  --dry: nothing written")
        return
    if drop:
        bak = path + ".pruned"
        new_file = not os.path.exists(bak)
        with open(bak, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LINE_COLS, extrasaction="ignore")
            if new_file:
                w.writeheader()
            w.writerows(drop)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LINE_COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(keep)
        print(f"  removed rows kept in {bak}")
    # The season tables are derived; leaving them behind means the viewer keeps
    # offering a league the cache no longer holds.
    killed = []
    for pat in ("season_", "lastday_"):
        for fp in glob.glob(os.path.join(OUT, pat + "*.csv")):
            stem = os.path.basename(fp).lower()
            if any(re.sub(r"[^a-z0-9]", "", x) in re.sub(r"[^a-z0-9]", "", stem)
                   for x in low):
                try:
                    os.remove(fp)
                    killed.append(os.path.basename(fp))
                except OSError:
                    pass
    if killed:
        print(f"  removed {len(killed)} derived file(s):")
        for k in killed[:8]:
            print(f"     {k}")

    if block:
        # Scoped to this season by default: --block-forever for the other kind.
        yr = None if "--block-forever" in sys.argv else year
        n = add_blocked(frags, yr)
        if yr:
            print(f"  {n} fragment(s) blocked FOR {yr} ONLY, in {BLOCKED_FILE}.")
            print(f"  They will not come back into the {yr} cache however often")
            print("  a backfill runs -- and next season is unaffected.")
        else:
            print(f"  {n} fragment(s) blocked in EVERY season, in {BLOCKED_FILE}.")
    else:
        print("  NOT blocked: a backfill may pull them back in, because the slug")
        print("  filter matches TEAMS and a club plays in several competitions.")
        print(f"  --block keeps them out of {year} only; next season is unaffected.")


BOX_URL_RE = re.compile(
    r"/(international|wnba|national)/boxscore/(\d{4}-\d{2}-\d{2})/([^/?#]+)/(\d+)", re.I)


def fetch_urls(paths_or_urls):
    """
    Fetch an explicit list of box scores, whatever dates or seasons they span.

    Scanning a whole year of indexes to find forty-eight games is absurd when the
    games are already known -- an AmeriCup qualifying cycle is two dozen fixtures
    scattered over thirteen months, and the index pages between them are almost
    all somebody else's basketball.

    Each url carries its own section, date and id, so every game is routed to the
    cache for ITS year rather than the one that happens to be on the command
    line. The slug filter and the --leagues filter are both bypassed: naming a
    game is a stronger statement of intent than either.
    """
    urls = []
    for item in paths_or_urls:
        if os.path.exists(item):
            with open(item, encoding="utf-8") as f:
                urls += [l.strip() for l in f if l.strip() and not l.startswith("#")]
        else:
            urls.append(item.strip())
    jobs = []
    for u in urls:
        m = BOX_URL_RE.search(u)
        if m:
            jobs.append((m.group(1).lower(), m.group(2), m.group(3), m.group(4)))
        elif u:
            print(f"  not a box score url, skipped: {u[:70]}")
    if not jobs:
        print("no usable urls")
        return
    by_year = {}
    for sec, date, slug, gid in jobs:
        by_year.setdefault((sec, date[:4]), []).append((date, slug, gid))
    print(f"rgm_gr {VERSION}  fetching {len(jobs)} named game(s)")
    for (sec, yr), items in sorted(by_year.items()):
        print(f"  {sec} {yr}: {len(items)} game(s)")

    blocked = load_blocked()
    total = 0
    for (sec, yr), items in sorted(by_year.items()):
        path = cache_path(sec, int(yr))
        seen, fixtures = set(), set()
        if os.path.exists(path):
            align_cache(path, quiet=True)
            with open(path, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    seen.add(r["game_id"])
                    fixtures.add(fixture_key(r["date"], r["team"], r["opp"]))
        rows, n = [], 0
        for date, slug, gid in sorted(items):
            if gid in seen:
                continue
            url = f"{RGM.BASE}/{sec}/boxscore/{date}/{slug}/{gid}"
            html = fetch_retry(url)
            if not html:
                continue
            lid, league, teams = RGM.parse_box(html, slug, sec)
            if len(teams) != 2:
                print(f"    {gid}: no box score tables")
                continue
            hit = is_blocked(league, blocked)
            if hit:
                print(f"    {gid}: {league} is blocked ({hit})")
                continue
            tt = []
            for t in teams:
                tot = t.get("totals") or {}
                def pair(v):
                    mm = re.match(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$", str(v or ""))
                    return (int(mm.group(1)), int(mm.group(2))) if mm else (0, 0)
                _, fga = pair(tot.get("fgm-a"))
                _, fta = pair(tot.get("ftm-a"))
                d = {"fga": fga, "fta": fta, "tov": num(tot.get("to")),
                     "oreb": num(tot.get("off")), "pts": num(tot.get("pts"))}
                if not d["fga"]:
                    for p in t["players"]:
                        d["fga"] += num(p.get("fga")); d["fta"] += num(p.get("fta"))
                        d["tov"] += num(p.get("tov")); d["oreb"] += num(p.get("oreb"))
                        d["pts"] += num(p.get("pts"))
                tt.append(d)
            try:
                poss = team_possessions(tt[0], tt[1])
            except Exception:                            # noqa: BLE001
                poss = 0.0
            if poss <= 0:
                print(f"    {gid}: no possessions, skipped")
                continue
            fk = fixture_key(date, teams[0]["team"], teams[1]["team"])
            if fk in fixtures:
                print(f"    {gid}: same fixture already held, skipped")
                continue
            fixtures.add(fk)
            phase = RGM.game_phase(html)
            sc = [t.get("score") for t in teams]
            if None in sc:
                sc = [tt[0]["pts"], tt[1]["pts"]]
            if None in sc or sc[0] == sc[1] == 0:
                print(f"    {gid}: no score, skipped")
                continue
            for i, t in enumerate(teams):
                won = (sc[0] > sc[1]) if i == 0 else (sc[1] > sc[0])
                for p in t["players"]:
                    if parse_minutes(p.get("min", "0")) <= 0:
                        continue
                    rows.append({
                        "game_id": gid, "date": date, "section": sec,
                        "league": league, "phase": phase, "team": t["team"],
                        "opp": teams[1 - i]["team"], "won": int(won),
                        "player": p.get("name", ""), "player_id": p.get("id", ""),
                        "player_url": (f"{RGM.BASE}/player/{p['slug']}/Summary/{p['id']}"
                                       if p.get("slug") and p.get("id") else ""),
                        "min": round(parse_minutes(p.get("min", "0")), 2),
                        "pts": num(p.get("pts")), "reb": num(p.get("reb")),
                        "ast": num(p.get("ast")), "stl": num(p.get("stl")),
                        "blk": num(p.get("blk")), "tov": num(p.get("tov")),
                        "pf": num(p.get("pf")), "fgm": num(p.get("fgm")),
                        "fga": num(p.get("fga")), "ftm": num(p.get("ftm")),
                        "fta": num(p.get("fta")), "oreb": num(p.get("oreb")),
                        "poss": round(poss, 3), "url": url})
            seen.add(gid)
            n += 1
            note_added({"added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "urls", "section": sec, "league": league,
                        "phase": phase, "date": date, "game_id": gid,
                        "teams": f"{teams[0]['team']} v {teams[1]['team']}",
                        "score": f"{sc[0]}-{sc[1]}", "url": url,
                        "lines": sum(1 for r in rows if r["game_id"] == gid)})
            print(f"    {date}  {league or '?'}: {teams[0]['team']} v "
                  f"{teams[1]['team']}  {sc[0]}-{sc[1]}")
        if rows:
            flush(path, rows)
            total += n
            print(f"  -> {n} game(s) into {os.path.basename(path)}")
    print(f"\n{total} game(s) added. Rate them with:")
    yrs = sorted({int(y) for _s, y in by_year})
    for sec in sorted({s for s, _y in by_year}):
        print(f"   python rgm_gr.py --season --section {sec} "
              f"--years {','.join(str(y) for y in yrs)} --min-gp 1")


def coverage(section, year, dfrom, dto, fetch=False, match=None):
    """
    Which games in the index are NOT in the cache, and would the filter take
    them now?

    The slug filter seeds from the cache, so early in a backfill it knew few
    teams and skipped any game where neither side was recognised -- a club whose
    season ended before the seeded window is invisible on the first pass. The
    seed grows as the run proceeds, which means a SECOND pass finds games the
    first one walked past. This reports that gap rather than leaving it to be
    guessed at, and --fetch closes it.

    Only index pages are read to produce the report: one request per date.
    """
    path = cache_path(section, year)
    held = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            held = {r["game_id"] for r in csv.DictReader(f)}
    known = known_teams(section, year, WANTED[0])
    blocked = load_blocked(year)
    # The version and the active filter, on the first line. Two rounds were lost
    # to a stale copy producing output identical to the new build: nothing on
    # screen distinguished "the filter found nothing" from "this file has no
    # filter".
    print(f"rgm_gr {VERSION}  coverage: {section} {dfrom}..{dto}")
    print(f"  {len(held)} game(s) cached, {len(known)} team name(s) known"
          + (f", matching {match!r}" if match else ", no --match filter"))

    # --match narrows the report to fixtures whose slug contains a fragment: one
    # team, or one league's clubs. Without it a report on a busy section is
    # thousands of rows of other people's basketball.
    frag = (match or "").strip().lower().replace(" ", "-")
    missing, would, wouldnt, days = [], 0, 0, 0
    for date in dates_between(dfrom, dto):
        played, _sched = RGM.day_games(date, section)
        if not played:
            continue
        gap = [(g, sl) for g, sl in played if g not in held]
        if frag:
            gap = [(g, sl) for g, sl in gap if frag in sl.lower()]
        if not gap:
            continue
        days += 1
        for g, sl in gap:
            ok = slug_wanted(sl, known)
            would += 1 if ok else 0
            wouldnt += 0 if ok else 1
            missing.append((date, g, sl, ok))
    print(f"\n  {len(missing)} game(s) in the index but not cached, "
          f"over {days} date(s)")
    print(f"  {would} the filter would take now, {wouldnt} it would still skip")
    # Every match when a fragment was given: the point of asking about one team
    # is to see all of its gaps, not the first handful.
    cap = 400 if frag else 25
    shown = [m for m in missing if m[3]][:cap]
    if shown:
        print(f"\n  would be fetched (first {len(shown)}):")
        for date, g, sl, _ in shown:
            print(f"     {date}  {g:<9} {sl[:58]}")
    skip = [m for m in missing if not m[3]][:10]
    if skip:
        print(f"\n  still skipped -- neither team is known, so these are other")
        print(f"  leagues as far as the filter can tell:")
        for date, g, sl, _ in skip:
            print(f"     {date}  {g:<9} {sl[:58]}")
    if os.path.exists(FAILED):
        with open(FAILED, encoding="utf-8") as f:
            n = len([l for l in f if l.strip()])
        if n:
            print(f"\n  {n} url(s) in failed.txt never came back either")
    if fetch and would:
        print(f"\n  fetching the {would} the filter accepts...")
        backfill(section, dfrom, dto, year)
    elif would:
        print(f"\n  add --fetch to collect them, or just re-run the backfill:")
        print(f"     python rgm_gr.py --backfill --section {section} "
              f"--year {year} --from {dfrom} --to {dto}")


def refetch(fragment, section=None, dry=False):
    """
    Re-read the games of one competition with the current parser.

    A parser fix cannot reach games already cached: the backfill skips a game id
    it has seen, so an improvement to how the competition name is read leaves
    every earlier game labelled the old way. That is why the Euro Championship
    for Small Countries games stayed "National Teams" after the reader learnt to
    find the tournament link.

    Each cached line stores the url it came from, so the games can be named
    exactly -- their rows are removed and those same urls fetched again. No index
    scanning, and nothing outside the named competition is touched.
    """
    frag = (fragment or "").strip().lower()
    if not frag:
        print('  --refetch needs a league fragment, e.g. --refetch "National Teams"')
        return
    sections = [section] if section else list(RGM.SECTIONS)
    urls, per_year = [], {}
    for sec in sections:
        for y in cached_years(sec):
            path = cache_path(sec, y)
            align_cache(path, quiet=True)
            with open(path, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            hit = [r for r in rows if frag in (r.get("league") or "").lower()]
            if not hit:
                continue
            u = sorted({r["url"] for r in hit if r.get("url")})
            no_url = len({r["game_id"] for r in hit if not r.get("url")})
            print(f"  {sec} {y}: {len(hit)} line(s), "
                  f"{len({r['game_id'] for r in hit})} game(s), {len(u)} url(s)"
                  + (f", {no_url} game(s) with no url stored" if no_url else ""))
            urls += u
            per_year[(sec, y)] = (rows, hit)
    if not urls:
        print(f"  nothing cached for a league matching {fragment!r}")
        return
    if dry:
        print(f"\n  --dry: would remove {sum(len(h) for _r, h in per_year.values())} "
              f"line(s) and re-fetch {len(urls)} game(s)")
        for u in urls[:10]:
            print(f"     {u}")
        return
    # Remove first, so the re-fetch does not see them as already held.
    for (sec, y), (rows, hit) in per_year.items():
        drop_ids = {r["game_id"] for r in hit}
        keep = [r for r in rows if r["game_id"] not in drop_ids]
        path = cache_path(sec, y)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LINE_COLS, extrasaction="ignore")
            w.writeheader(); w.writerows(keep)
        print(f"  {os.path.basename(path)}: {len(rows)} -> {len(keep)} line(s)")
    print()
    fetch_urls(urls)


def tidy(dry=False):
    """
    One derived file per competition. Delete the rest.

    The season label moves as a cache grows -- "2025" becomes "2024-25" when an
    earlier window is fetched -- and the write step only supersedes files it
    happens to regenerate in that run. A competition rated under one label and
    then another leaves both on disk, and the viewer reads whatever it finds, so
    every player and team in it appears twice.

    Newest file wins, per (prefix, section, competition). Nothing else is
    touched: caches, added.csv and the block lists are left alone.
    """
    groups = {}
    for pre in ("season_", "lastday_", "standings_"):
        for p in glob.glob(os.path.join(OUT, pre + "*.csv")):
            base = os.path.basename(p)
            m = re.match(re.escape(pre) + r"(international|wnba|national)-(.+)\.csv$", base)
            if not m:
                continue
            sec, rest = m.group(1), m.group(2)
            # Strip the season label from the front of the remainder: it is
            # digits and hyphens up to the first word.
            lg = re.sub(r"^(\d{4}(?:-\d{2,4})?)-", "", rest)
            groups.setdefault((pre, sec, lg), []).append(p)
    removed = 0
    for (pre, sec, lg), paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        keep, drop = paths[0], paths[1:]
        print(f"  {pre}{sec} / {lg}")
        print(f"     keep  {os.path.basename(keep)}")
        for p in drop:
            print(f"     drop  {os.path.basename(p)}")
            if not dry:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
    if not groups or not removed:
        print("  nothing duplicated" if not dry else "  (dry run)")
    else:
        print(f"\n  removed {removed} superseded file(s)")
    return removed


def newest_rated(section):
    """The latest game date any rated table for this section covers."""
    best = ""
    for p in glob.glob(os.path.join(OUT, f"lastday_{section}-*.csv")):
        try:
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    d = (r.get("date") or "")
                    if d > best:
                        best = d
        except OSError:
            pass
    return best


def newest_cached(section):
    """The latest game date in this section's lines caches."""
    best = ""
    for y in cached_years(section):
        p = cache_path(section, y)
        try:
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    d = (r.get("date") or "")
                    if d > best:
                        best = d
        except OSError:
            pass
    return best


def auto(force=False):
    """
    Re-rate whatever has fallen behind, and nothing else.

    Games reach the cache the moment the tracker reads them; ratings only move
    when the season pass runs. That gap is what puts "cached but not yet rated"
    on the viewer, and asking a person to notice a banner and type a command is a
    poor way to close it. This compares the newest CACHED game date against the
    newest RATED one, per section, and rates only the sections that differ.

    Cheap when there is nothing to do -- it reads the caches and writes nothing --
    so it is safe to run every few minutes from Task Scheduler.
    """
    print(f"rgm_gr {VERSION}  auto")
    # Against the LAST RUN, not against the newest rated date.
    #
    # Comparing cached-to-rated looks right and is not: the newest cached game
    # may be one that will never be rated -- a blocked league, a cup final pulled
    # out by the id rule -- and then the two dates can never converge and every
    # run re-rates everything forever. What actually needs answering is "has the
    # cache changed since I last looked", so that is what is stored.
    statef = os.path.join(OUT, "auto_state.json")
    state = {}
    if os.path.exists(statef):
        try:
            with open(statef, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
    did = 0
    for section in ("wnba", "international", "national"):
        if not cached_years(section):
            continue
        cached, rated = newest_cached(section), newest_rated(section)
        if not cached:
            continue
        # The row count as well as the date: games are often added for a date
        # already present, and a date alone would miss those.
        size = sum(os.path.getsize(cache_path(section, y))
                   for y in cached_years(section)
                   if os.path.exists(cache_path(section, y)))
        sig = f"{cached}|{size}"
        if state.get(section) == sig and not force:
            print(f"  {section:<14} unchanged since the last run "
                  f"(cached to {cached}, rated to {rated or 'nothing'})")
            continue
        state[section] = sig
        print(f"  {section:<14} cached to {cached}, rated to "
              f"{rated or 'nothing'} -- rating now")
        # national competitions span years (qualifying cycles), the others do not
        yrs = cached_years(section) if section == "national" else None
        y = max(cached_years(section))
        season(section, y, min_gp=1, years=yrs)
        did += 1
    try:
        with open(statef, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass
    if not did:
        print("\n  nothing to do")
    return did


def catchup(section, days=5, year=None):
    """
    Fetch the last few days for a section, then rate them.

    Exists because the two halves of this system dedupe against different
    records. rgm_tracker skips a game already in ITS state -- so any game it read
    before it started writing box-score lines will never produce lines, however
    often it polls. The backfill dedupes against the LINES cache instead, so it
    fills precisely those gaps.

    That is also why "Last day" can sit a day behind after everything looks
    updated: the games are known, the lines are not.
    """
    today = datetime.date.today()
    y = year or today.year
    start = (today - datetime.timedelta(days=max(1, days) - 1))
    if start.year != y:
        start = datetime.date(y, 1, 1)
    print(f"catchup: {section} {start.isoformat()}..{today.isoformat()}")
    backfill(section, start.isoformat(), today.isoformat(), y)
    print()
    season(section, y, min_gp=1)


def prune(section, year, wanted):
    """
    Drop cached lines from leagues you are not tracking.

    The seed for the slug filter IS the cache, so one unfiltered run taught it
    every league RealGM covers -- thirty-five of them -- and the filter stopped
    filtering: Spanish ACB and Chinese CBA were "recognised" as worth fetching.
    Narrowing the cache narrows the seed, and the next backfill skips them
    without opening a page.

    Removed rows go to a .pruned file rather than being deleted. Re-fetching a
    season is expensive and a mind can be changed.
    """
    if not wanted:
        print('  --prune needs --leagues, e.g. --leagues "PBA,LNBP,Puerto Rican"')
        return
    path = cache_path(section, year)
    if not os.path.exists(path):
        print(f"no cache at {path}")
        return
    want = [w.strip().lower() for w in wanted.split(",") if w.strip()]
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    keep, drop = [], []
    for r in rows:
        lg = (r.get("league") or "").lower()
        (keep if any(w in lg for w in want) else drop).append(r)
    kept_lg = sorted({r["league"] for r in keep})
    drop_lg = sorted({r["league"] for r in drop})
    print(f"prune: {len(rows)} line(s) -> {len(keep)} kept, {len(drop)} removed")
    print(f"  keeping ({len(kept_lg)}): " + ", ".join(kept_lg))
    if drop_lg:
        print(f"  removing ({len(drop_lg)}): " + ", ".join(drop_lg[:14])
              + (" ..." if len(drop_lg) > 14 else ""))
    if not keep:
        print("  !! that would empty the cache -- check the --leagues spelling")
        return
    bak = path + ".pruned"
    new_file = not os.path.exists(bak)
    with open(bak, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LINE_COLS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(drop)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LINE_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(keep)
    print(f"  removed rows kept in {bak}")
    print(f"  the slug filter now seeds from {len(kept_lg)} league(s)")


def backfill(section, dfrom, dto, year):
    path = cache_path(section, year)
    seen, fixtures = set(), set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                seen.add(r["game_id"])
                fixtures.add(fixture_key(r["date"], r["team"], r["opp"]))
    print(f"rgm_gr {VERSION}  backfill {section} {dfrom}..{dto}")
    print(f"  cache: {path}  ({len(seen)} game(s) already held)")

    blocked = load_blocked(year)
    n_blocked = [0]
    if blocked:
        print(f"  blocking {len(blocked)} league fragment(s): "
              + ", ".join(blocked[:8]) + (" ..." if len(blocked) > 8 else ""))
    known = known_teams(section, year, WANTED[0])
    if known:
        lgs = sorted(set(known.values()))
        print(f"  filtering to {len(lgs)} known league(s) via {len(known)} team "
              f"name(s) in the slug -- other games are skipped WITHOUT fetching")
        for l in lgs:
            print(f"     {l}")
    else:
        print("  no cache yet, so nothing to filter against: fetching everything.")
        print("  Run a short window first, then widen -- the second pass will")
        print("  know which teams matter and skip the rest.")

    # The cache file AND the season label both come from --year, so a range
    # that crosses a year boundary would file January 2025 games as season 2026
    # and nothing downstream would ever notice. Run one pass per season instead.
    yrs = sorted({dfrom[:4], dto[:4]})
    if len(yrs) > 1 or yrs[0] != str(year):
        print(f"  !! --year {year} but the range covers {', '.join(yrs)}.")
        print("     Every game would be filed under season "
              f"{year} regardless of when it was played.")
        print("     Run one pass per season:")
        for y in yrs:
            a = dfrom if y == dfrom[:4] else f"{y}-01-01"
            b = dto if y == dto[:4] else f"{y}-12-31"
            print(f"       python rgm_gr.py --backfill --section {section} "
                  f"--year {y} --from {a} --to {b}")
        print("     (or pass --force-year to file them all under "
              f"{year} anyway)")
        if "--force-year" not in sys.argv:
            return

    dates = dates_between(dfrom, dto)
    new_rows, n_games, n_days = [], 0, 0
    for date in dates:
        played, sched = RGM.day_games(date, section)
        if not played and not sched:
            continue
        n_days += 1
        todo = [(g, s) for g, s in played if g not in seen]
        skipped = 0
        if known:
            before = len(todo)
            todo = [(g, sl) for g, sl in todo if slug_wanted(sl, known)]
            skipped = before - len(todo)
        print(f"  {date}: {len(played)} played, {len(todo)} to fetch"
              + (f", {skipped} skipped (other leagues)" if skipped else ""))
        for gid, slug in todo:
            url = f"{RGM.BASE}/{section}/boxscore/{date}/{slug}/{gid}"
            html = fetch_retry(url)
            if not html:
                continue
            lid, league, teams = RGM.parse_box(html, slug, section)
            if len(teams) != 2:
                continue
            hit = is_blocked(league, blocked)
            if hit:
                # Recorded as seen so it is never fetched twice, but no lines are
                # kept: a known team playing in an unwanted competition is the
                # case this exists for.
                seen.add(gid)
                n_blocked[0] += 1
                continue
            tt = []
            for t in teams:
                tot = t.get("totals") or {}
                def pair(v):
                    m = re.match(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$", str(v or ""))
                    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
                _, fga = pair(tot.get("fgm-a"))
                _, fta = pair(tot.get("ftm-a"))
                d = {"fga": fga, "fta": fta, "tov": num(tot.get("to")),
                     "oreb": num(tot.get("off")), "pts": num(tot.get("pts"))}
                if not d["fga"]:
                    # No totals row: sum the players and accept the small
                    # undercount from team rebounds and team turnovers.
                    for p in t["players"]:
                        d["fga"] += num(p.get("fga")); d["fta"] += num(p.get("fta"))
                        d["tov"] += num(p.get("tov")); d["oreb"] += num(p.get("oreb"))
                        d["pts"] += num(p.get("pts"))
                tt.append(d)
            try:
                poss = team_possessions(tt[0], tt[1])
            except Exception:                                # noqa: BLE001
                poss = 0.0
            if poss <= 0:
                print(f"    {gid}: no possessions, skipped")
                continue
            phase = RGM.game_phase(html)
            fk = fixture_key(date, teams[0]["team"], teams[1]["team"])
            if fk in fixtures:
                print(f"    {gid}: same fixture as one already held, skipped")
                seen.add(gid)
                continue
            fixtures.add(fk)
            # Learn both teams, so a club first seen today is recognised
            # tomorrow without another full sweep.
            if known and league:
                for t in (teams[0]["team"], teams[1]["team"]):
                    k = re.sub(r"[^a-z0-9]", "", (t or "").lower())
                    if k:
                        known[k] = league
            sc = [t.get("score") for t in teams]
            if None in sc:
                # No line score on the page -- postponed, abandoned, or a layout
                # RealGM has not settled. The totals rows still have points, and
                # a winner is a winner however it is counted, so fall back to
                # those rather than discard the game.
                sc = [tt[0]["pts"], tt[1]["pts"]]
            if None in sc or sc[0] == sc[1] == 0:
                # Still nothing: the win/loss multiplier has no answer, and
                # guessing one would quietly mark everybody a loser.
                print(f"    {gid}: no score, skipped")
                continue
            for i, t in enumerate(teams):
                won = (sc[0] > sc[1]) if i == 0 else (sc[1] > sc[0])
                for p in t["players"]:
                    if parse_minutes(p.get("min", "0")) <= 0:
                        continue
                    new_rows.append({
                        "game_id": gid, "date": date, "section": section,
                        "league": league, "phase": phase, "team": t["team"],
                        "opp": teams[1 - i]["team"], "won": int(won),
                        "player": p.get("name", ""), "player_id": p.get("id", ""),
                        "player_url": (f"{RGM.BASE}/player/{p['slug']}/Summary/{p['id']}"
                                       if p.get("slug") and p.get("id") else ""),
                        "min": round(parse_minutes(p.get("min", "0")), 2),
                        "pts": num(p.get("pts")), "reb": num(p.get("reb")),
                        "ast": num(p.get("ast")), "stl": num(p.get("stl")),
                        "blk": num(p.get("blk")), "tov": num(p.get("tov")),
                        "pf": num(p.get("pf")), "fgm": num(p.get("fgm")),
                        "fga": num(p.get("fga")), "ftm": num(p.get("ftm")),
                        "fta": num(p.get("fta")), "oreb": num(p.get("oreb")),
                        "poss": round(poss, 3), "url": url})
            seen.add(gid)
            n_games += 1
            note_added({
                "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "backfill", "section": section, "league": league,
                "phase": phase, "date": date, "game_id": gid,
                "teams": f"{teams[0]['team']} v {teams[1]['team']}",
                "score": f"{sc[0]}-{sc[1]}", "url": url,
                "lines": sum(1 for r in new_rows if r["game_id"] == gid)})
            # Written as we go: a backfill is hundreds of requests and losing
            # them to one failure at game 250 would be its own bug.
            if len(new_rows) >= 400:
                flush(path, new_rows); new_rows = []
    if new_rows:
        flush(path, new_rows)
    if n_blocked[0]:
        print(f"  {n_blocked[0]} game(s) discarded as blocked leagues")
    print(f"\n  {n_games} new game(s) over {n_days} day(s) with games")
    print(f"  -> {path}")
    if os.path.exists(FAILED):
        with open(FAILED, encoding="utf-8") as f:
            n = len([l for l in f if l.strip()])
        if n:
            print(f"  !! {n} url(s) in {FAILED} never came back. Re-running the")
            print(f"     backfill retries them -- games already held are skipped,")
            print(f"     so it costs only the missing ones.")


# Column sets, oldest last. A cache written before a column was added has a
# shorter header, and appending to it is what corrupted one: DictWriter writes
# values in the order of the fieldnames it is GIVEN, not the order the file's
# header declares, so adding player_url shifted every later row by one and a url
# ended up under "min".
LINE_COLS_V1 = ["game_id", "date", "section", "league", "phase", "team", "opp",
                "won", "player", "player_id", "min", "pts", "reb", "ast", "stl",
                "blk", "tov", "pf", "fgm", "fga", "ftm", "fta", "oreb", "poss",
                "url"]
KNOWN_LAYOUTS = [LINE_COLS, LINE_COLS_V1]


def align_cache(path, quiet=False):
    """
    Rewrite a lines cache so every row matches the current columns.

    Rows are identified by their FIELD COUNT, which is unambiguous here because
    each layout has a different width. A row is then re-read against the layout
    it was actually written with, and written back against the current one.
    """
    if not os.path.exists(path):
        return 0, 0
    with open(path, encoding="utf-8") as f:
        raw = list(csv.reader(f))
    if not raw:
        return 0, 0
    header, body = raw[0], [r for r in raw[1:] if r]
    if header == LINE_COLS and all(len(r) == len(LINE_COLS) for r in body):
        return 0, len(body)                      # already correct
    fixed, dropped = [], 0
    for r in body:
        layout = next((L for L in KNOWN_LAYOUTS if len(r) == len(L)), None)
        if layout is None:
            dropped += 1
            continue
        d = dict(zip(layout, r))
        fixed.append({c: d.get(c, "") for c in LINE_COLS})
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LINE_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(fixed)
    os.replace(tmp, path)
    if not quiet:
        print(f"  realigned {os.path.basename(path)}: {len(fixed)} row(s) rewritten"
              + (f", {dropped} unrecognisable row(s) dropped" if dropped else ""))
    return len(fixed), dropped


def flush(path, rows):
    # Align before appending, or the header and the values disagree again.
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            first = f.readline().rstrip("\r\n")
        if first.split(",") != LINE_COLS:
            align_cache(path)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LINE_COLS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- season

def gp_mult(ratio):
    if GP_MODE == "none":
        return 1.0
    if GP_MODE == "linear":
        return ratio
    if GP_MODE == "exponent":
        return ratio ** GP_EXPONENT
    raise ValueError(f"unknown GP_MODE {GP_MODE!r}")


# Preseason is excluded by default. The 2026 WNBA regular season began on 8 May
# and RealGM indexes the exhibition games before it on the same pages -- rating
# those alongside the real thing pads every season total, and a date range cannot
# separate them because they overlap nothing. --phase overrides.
DROP_PHASES = {"preseason", "exhibition", "cup"}

# A game whose id is this far from the NEAREST other id in the season is not part
# of the same fixture list.
#
# The WNBA Commissioner's Cup final is indistinguishable from a regular-season
# game on RealGM -- same layout, same "Regular Season" header, no separate label
# anywhere on the page. The only tell is its id: the 2026 season runs 158365 to
# 158561 and the Cup final is 160027. It does not count towards regular-season
# totals, so it made every Liberty player read one game high against RealGM's own
# numbers.
#
# ISOLATION, not range. Toronto Tempo's fixtures also sit outside the main block
# (158660, 158673) because an expansion team's games were created later -- but
# they are 13 apart from each other, so a rule about absolute distance would
# wrongly discard a whole club's season. What marks the Cup final is having no
# neighbour at all.
ID_ISOLATION = 500

# Games to leave out, by id. A blunt instrument, and the right one here.
#
# The isolation rule above was supposed to catch the WNBA Commissioner's Cup
# final automatically. It does not: 160027 looked isolated among the Liberty's
# own games but has neighbours within 500 once the whole league's ids are in the
# cache. RealGM gives that game no distinguishing label -- same layout, same
# "Regular Season" header -- so no rule reading the page can find it either.
#
# One game a season, and you know which one. A list you control beats a
# heuristic that quietly gets it wrong, and --ids shows the candidates.
EXCLUDE_FILE = os.path.join(OUT, "exclude_ids.txt")


# Per-line corrections, in data rather than code.
#
# RealGM mis-attributes a line now and then: in the 18 May Washington v Dallas
# game a Wings line is filed under Lacy Sheldon when the performance was Jessica
# Shepard's. gr_season.py keeps the same kind of thing in bad_rows.json and
# person_alias.json for the NBA side -- corrections belong in a file that can be
# appended to without touching the pipeline.
#
# Each entry matches on any subset of date / team / player / game_id and rewrites
# the player. An id is looked up from that player's other games when not given,
# so a corrected line groups with the rest of their season instead of becoming a
# second player.
FIXES_FILE = os.path.join(OUT, "fixes.json")

FIXES_TEMPLATE = [
    {"date": "2026-05-18", "team": "Dallas Wings",
     "player": "Jacy Sheldon", "to": "Jessica Shepard",
     "why": "RealGM filed a Wings line under Jacy Sheldon, a Mystics player"}
]


# Competition names that should be treated as one.
#
# A World Cup qualifying cycle runs across two calendar years and RealGM labels
# its stages separately -- "European World Cup Pre-Qualifier", "FIBA World Cup
# Qualifiers". Ranked as three competitions they give three tables of three games
# each; ranked as one cycle they give a table worth reading.
#
# One rule per line: FRAGMENT = canonical name. Longest fragment wins, so a
# specific rule can override a general one.
ALIAS_FILE = os.path.join(OUT, "league_alias.txt")

# EMPTY BY DEFAULT, and deliberately.
#
# A first version shipped with rules merging "World Cup Pre-Qualifier" and
# "World Cup Qualifiers" into one "FIBA World Cup Qualifying" -- which would also
# have merged the European and African qualifying tables into a single ranking.
# That is worse than the fragmentation it was meant to fix: qualifying is played
# by continent, and a European group and an African group are not one
# competition. Combining CALENDAR YEARS is what was wanted, and --years does that
# on its own without touching the names.
#
# So nothing is grouped unless you say so. Run --season, read the competition
# names RealGM actually uses, then add rules that keep the continent in the
# canonical name.
ALIAS_TEMPLATE = """# Group competitions under one name:  FRAGMENT = canonical name
# Longest matching fragment wins. Nothing is grouped until you add a rule.
#
# KEEP THE CONTINENT IN THE NAME. Qualifying is played by region, so merging
# across regions would rank a European group against an African one:
#
#   European World Cup Pre-Qualifier = European World Cup Qualifying
#   European World Cup Qualifiers    = European World Cup Qualifying
#   African World Cup Qualifiers     = African World Cup Qualifying
#   Americas World Cup Qualifiers    = Americas World Cup Qualifying
#
# Combining 2025 and 2026 needs no rule at all -- that is --years 2025,2026.
"""


def load_aliases():
    if not os.path.exists(ALIAS_FILE):
        try:
            with open(ALIAS_FILE, "w", encoding="utf-8") as f:
                f.write(ALIAS_TEMPLATE)
            print(f"  wrote a starter {ALIAS_FILE}")
        except OSError:
            pass
    out = []
    try:
        with open(ALIAS_FILE, encoding="utf-8") as f:
            for line in f:
                t = line.split("#")[0].strip()
                if "=" in t:
                    frag, canon = t.split("=", 1)
                    frag, canon = frag.strip(), canon.strip()
                    if frag and canon:
                        out.append((frag.lower(), canon))
    except OSError:
        pass
    out.sort(key=lambda x: -len(x[0]))      # longest fragment wins
    return out


def apply_aliases(rows):
    al = load_aliases()
    if not al:
        return 0
    hits, seen = 0, {}
    for r in rows:
        lg = (r.get("league") or "")
        low = lg.lower()
        for frag, canon in al:
            if frag in low and lg != canon:
                seen.setdefault(canon, set()).add(lg)
                r["league"] = canon
                hits += 1
                break
    for canon, froms in sorted(seen.items()):
        if len(froms) > 1 or True:
            print(f"  grouped as {canon!r}: " + ", ".join(sorted(froms)))
    return hits


def load_fixes():
    if not os.path.exists(FIXES_FILE):
        try:
            with open(FIXES_FILE, "w", encoding="utf-8") as f:
                json.dump(FIXES_TEMPLATE, f, indent=2, ensure_ascii=False)
            print(f"  wrote a starter {FIXES_FILE}")
        except OSError:
            pass
        return list(FIXES_TEMPLATE)
    try:
        with open(FIXES_FILE, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, list) else []
    except (OSError, ValueError) as e:
        print(f"  !! {FIXES_FILE} unreadable ({e}) -- no fixes applied")
        return []


def apply_fixes(rows):
    """-> (rows, n_changed). Reports every change; silence would be worse."""
    fixes = load_fixes()
    if not fixes:
        return rows, 0
    # name -> the id it carries elsewhere, so a renamed line joins the right player
    ids = {}
    for r in rows:
        nm = (r.get("player") or "").strip().lower()
        if nm and r.get("player_id"):
            ids.setdefault(nm, r["player_id"])
    n = 0
    for fx in fixes:
        to = (fx.get("to") or "").strip()
        if not to:
            continue
        hits = []
        for r in rows:
            ok = True
            for k in ("date", "team", "player", "game_id"):
                want = fx.get(k)
                if want and (r.get(k) or "").strip().lower() != str(want).strip().lower():
                    ok = False
                    break
            if ok:
                hits.append(r)
        if not hits:
            # Say what IS there on that date, so a near-miss on the spelling is
            # obvious rather than silent. "Lacy Sheldon" may be "Lucy Sheldon" or
            # anything else in RealGM's own rendering.
            print(f"  fix NOT matched: {fx.get('player')!r} -> {to!r} "
                  f"({fx.get('date')} {fx.get('team')})")
            near = sorted({r.get("player") for r in rows
                           if (not fx.get("date") or r.get("date") == fx.get("date"))
                           and (not fx.get("team")
                                or (r.get("team") or "").lower() == str(fx["team"]).lower())})
            if near:
                print(f"     players on that team that day: {', '.join(near[:14])}")
            else:
                print(f"     no lines at all for that team on that date")
            continue
        new_id = fx.get("to_id") or ids.get(to.lower()) or ""
        for r in hits:
            was = r.get("player")
            r["player"] = to
            if new_id:
                r["player_id"] = new_id
            n += 1
        print(f"  fixed {len(hits)} line(s): {was} -> {to}"
              f"  ({fx.get('date','')} {fx.get('team','')})"
              + (f"   [{fx['why']}]" if fx.get("why") else ""))
    return rows, n


def load_excludes():
    ids = set()
    if os.path.exists(EXCLUDE_FILE):
        try:
            with open(EXCLUDE_FILE, encoding="utf-8") as f:
                for line in f:
                    tok = line.split("#")[0].strip()
                    if tok.isdigit():
                        ids.add(tok)
        except OSError:
            pass
    return ids


# Which competitions run ACROSS calendar years, by name fragment.
#
# Qualifying is a cycle: the European World Cup pre-qualifiers played in February
# 2025 and the qualifiers played in 2026 are one competition, and rating each year
# alone gives two tables of two games. A tournament is not: EuroBasket U17 2025
# and EuroBasket U17 2026 are separate events with separate winners, and merging
# them would invent a competition that never happened.
#
# So --years combines years ONLY for competitions matching one of these. Anything
# else is rated per year even when several years are asked for.
CYCLE_FILE = os.path.join(OUT, "cycles.txt")
CYCLE_TEMPLATE = """# Competitions that span calendar years, by name fragment.
# Matching competitions are rated across all the years given to --years;
# everything else stays one table per year.
#
# Qualifying cycles span years. Tournaments do not -- EuroBasket U17 2025 and
# EuroBasket U17 2026 are different events.
Qualifier
Qualifiers
Qualifying
Pre-Qualifier
"""


def load_cycles():
    if not os.path.exists(CYCLE_FILE):
        try:
            with open(CYCLE_FILE, "w", encoding="utf-8") as f:
                f.write(CYCLE_TEMPLATE)
            print(f"  wrote a starter {CYCLE_FILE}")
        except OSError:
            pass
    out = []
    try:
        with open(CYCLE_FILE, encoding="utf-8") as f:
            for line in f:
                t = line.split("#")[0].strip()
                if t:
                    out.append(t.lower())
    except OSError:
        pass
    return out


def is_cycle(league, cycles=None):
    lg = (league or "").lower()
    return any(c in lg for c in (cycles if cycles is not None else load_cycles()))


def cached_years(section):
    """Every year with a lines cache for this section, ascending."""
    out = []
    for p in glob.glob(os.path.join(OUT, f"lines_{section}_*.csv")):
        m = re.search(r"_(\d{4})\.csv$", p)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def span_label(yrs):
    """
    [2025, 2026] -> "2025-26", the way a season is written.

    A single year stays as it is. A gap wider than consecutive keeps both years
    in full, because "2025-27" would read as a three-year span rather than two
    separate ones.
    """
    yrs = sorted(set(int(y) for y in yrs))
    if len(yrs) == 1:
        return str(yrs[0])
    lo, hi = yrs[0], yrs[-1]
    if hi - lo == 1:
        return f"{lo}-{str(hi)[2:]}"
    return f"{lo}-{hi}"


def season(section, year, min_gp=1, league_filter=None, want_html=False,
           top=200, phase=None, years=None):
    # One season, or several read together. A qualifying cycle runs across two
    # calendar years, and rating each year alone gives two tables of three games
    # where one table of six is the actual competition.
    yrs = years or [year]
    label = span_label(yrs)
    rows = []
    for y in yrs:
        path = cache_path(section, y)
        if not os.path.exists(path):
            print(f"  no cache for {y} at {os.path.basename(path)}")
            continue
        align_cache(path, quiet=True)
        with open(path, encoding="utf-8") as f:
            got = list(csv.DictReader(f))
        rows += got
        if len(yrs) > 1:
            print(f"  {y}: {len(got)} line(s)")
    if not rows:
        print(f"nothing cached for {section} {label} -- run --backfill first")
        return
    # Corrections and aliases FIRST, before any filtering. The league loop
    # re-enters this function with the canonical name, and if aliasing ran after
    # the filter that name matched nothing -- the rows still said
    # "European World Cup Pre-Qualifier".
    rows, _nfix = apply_fixes(rows)
    apply_aliases(rows)

    if league_filter:
        rows = [r for r in rows if league_filter.lower() in (r["league"] or "").lower()]

    # CORRECTIONS FIRST, before any dedupe.
    #
    # Ordering matters more than it looks. A mis-attributed line makes one player
    # appear twice on the same date -- Jacy Sheldon on Dallas and on Washington on
    # 18 May -- and the dedupe below treats that as a duplicate and drops one.
    # Run second, it would silently discard her real Mystics line and call the
    # job done. Run first, the Dallas line becomes Shepard and there is no
    # duplicate left to find.

    # Team names are not stable, and that broke the first dedupe. The WNBA slug
    # reads "Seattle-at-New-York", so a game resolved from the slug is filed
    # under "New York" while one resolved from a team link is "New York Liberty".
    # Different strings, so a re-listed fixture survived and the team-game
    # denominator split in two -- every Liberty player showed one game too many
    # against RealGM's own totals, while every other club matched exactly.
    #
    # So names are canonicalised: where one is a prefix of another, the longer
    # wins ("New York" -> "New York Liberty").
    canon = canon_map(r["team"] for r in rows)
    for r in rows:
        r["team"] = canon.get(r["team"].strip().lower(), r["team"])
        r["opp"] = canon.get((r.get("opp") or "").strip().lower(), r.get("opp"))
    variants = sum(1 for k, v in canon.items() if k != v.strip().lower())
    if variants:
        print(f"  merged {variants} team-name variant(s) "
              f"(slug short form vs full club name)")

    # And a player-game is keyed on the DATE, not the fixture. A player appears
    # at most once a day, which holds however the teams happen to be labelled.
    keep, seen_pl, dup = [], set(), 0
    for r in rows:
        k = (r.get("player_id") or r.get("player"), r.get("date"))
        if k in seen_pl:
            dup += 1
            continue
        seen_pl.add(k)
        keep.append(r)
    if dup:
        print(f"  dropped {dup} duplicate player-game line(s) "
              f"(same player, same date)")
    rows = keep

    # Explicit exclusions first: a named id always wins over any heuristic.
    excl = load_excludes() | {t.strip() for t in (arg("--exclude", "") or "").split(",") if t.strip().isdigit()}
    if excl:
        hit = [r for r in rows if r["game_id"] in excl]
        if hit:
            when = sorted({(r["date"], r["team"], r["opp"], r["game_id"]) for r in hit})
            print(f"  excluding {len(when)} named game(s):")
            for d, t, opp, g in when[:8]:
                print(f"     {d}  {t} v {opp}   ({g})")
            rows = [r for r in rows if r["game_id"] not in excl]
        else:
            print(f"  {len(excl)} id(s) listed for exclusion, none present in this cache")

    # Reclassify isolated ids before counting phases.
    # Needs a population before "isolated" means anything. With a handful of
    # games every id is far from every other one, and the rule discarded a whole
    # two-game league as a cup final. Thirty is enough to have a fixture block to
    # be isolated FROM.
    ID_MIN_GAMES = 30
    if "--keep-outliers" not in sys.argv:
        ids = sorted({int(r["game_id"]) for r in rows if str(r["game_id"]).isdigit()})
        if len(ids) < ID_MIN_GAMES:
            ids = []
        lone = set()
        for i, g in enumerate(ids):
            left = g - ids[i - 1] if i else 10 ** 9
            right = ids[i + 1] - g if i + 1 < len(ids) else 10 ** 9
            if min(left, right) > ID_ISOLATION:
                lone.add(str(g))
        if lone:
            hit = [r for r in rows if r["game_id"] in lone]
            when = sorted({(r["date"], r["team"], r["opp"]) for r in hit})
            print(f"  {len(lone)} game(s) with an isolated id -> treated as 'cup'"
                  f" (not regular season):")
            for d, t, opp in when[:6]:
                print(f"     {d}  {t} v {opp}")
            print(f"     nearest other id more than {ID_ISOLATION} away."
                  f" --keep-outliers to include them")
            for r in rows:
                if r["game_id"] in lone:
                    r["phase"] = "cup"

    have = collections.Counter((r.get("phase") or "?") for r in rows)
    if phase:
        rows = [r for r in rows if (r.get("phase") or "") == phase]
    else:
        dropped = [r for r in rows if (r.get("phase") or "") in DROP_PHASES]
        if dropped:
            rows = [r for r in rows if (r.get("phase") or "") not in DROP_PHASES]
            print(f"  excluded {len(dropped)} line(s) from "
                  f"{', '.join(sorted({d.get('phase') for d in dropped}))}"
                  f" -- pass --phase to include or isolate them")
    if not rows:
        print("no rows after filtering")
        return

    blocked = load_blocked(year)
    if blocked:
        before = len(rows)
        rows = [r for r in rows if not is_blocked(r.get("league"), blocked)]
        if before != len(rows):
            print(f"  {before - len(rows)} line(s) hidden from blocked league(s)")
    if not rows:
        print("everything in this cache is blocked")
        return

    leagues = collections.Counter(r["league"] for r in rows)
    if len(leagues) > 1 and not league_filter:
        # Not a refusal any more, but still never pooled: each league gets its own
        # table in its own file, one after another. A cache of international games
        # holds six or seven competitions, and demanding six separate runs was
        # friction with no safety benefit. What matters is that a PBA rating and a
        # WNBA rating never share a ranking, and looping preserves that exactly.
        print(f"{len(leagues)} leagues in this cache -- one table each:")
        for lg, c in leagues.most_common():
            print(f"   {c:>6} line(s)  {lg}")
        cyc = load_cycles()
        multi = len(yrs) > 1
        for lg, _c in leagues.most_common():
            if multi and not is_cycle(lg, cyc):
                # A tournament, not a cycle: one table per year it was played in,
                # each labelled with its own year.
                played = sorted({int(r["date"][:4]) for r in rows
                                 if r["league"] == lg and r.get("date")})
                for y in played or yrs:
                    print("\n" + "=" * 78)
                    season(section, y, min_gp=min_gp, league_filter=lg,
                           want_html=want_html, top=top, phase=phase, years=[y])
            else:
                print("\n" + "=" * 78)
                season(section, year, min_gp=min_gp, league_filter=lg,
                       want_html=want_html, top=top, phase=phase, years=years)
        return

    # Team games from the collected set, so a partial backfill measures everyone
    # against the same denominator.
    team_games = collections.defaultdict(set)
    for r in rows:
        team_games[r["team"]].add(r["game_id"])

    # Per-game values are kept as well as aggregated, so the most recent day on
    # record can be ranked. A season table cannot answer "how did last night go"
    # -- it has one row per player, not per game.
    per_game = []

    agg = {}
    for r in rows:
        pid = r["player_id"] or r["player"]
        g = GameLine(player=r["player"], team=r["team"],
                     minutes=float(r["min"] or 0), poss=float(r["poss"] or 0),
                     nrat=0.0, pts=num(r["pts"]), fgm=num(r["fgm"]),
                     fga=num(r["fga"]), ftm=num(r["ftm"]), fta=num(r["fta"]),
                     oreb=num(r["oreb"]), treb=num(r["reb"]), ast=num(r["ast"]),
                     stl=num(r["stl"]), blk=num(r["blk"]), tov=num(r["tov"]),
                     pf=num(r["pf"]))
        gr = game_score(g) * wl(r["won"] == "1")
        per_game.append((r, gr))
        a = agg.setdefault(pid, {
            "player": r["player"], "player_id": pid, "league": r["league"],
            "player_url": r.get("player_url", ""),
            "teams": set(), "gp": 0, "min": 0.0, "gr": 0.0, "best": None,
            "pts": 0, "reb": 0, "ast": 0, "w": 0})
        a["player"] = r["player"]
        if r.get("player_url"):
            a["player_url"] = r["player_url"]
        a["teams"].add(r["team"])
        a["gp"] += 1
        a["min"] += float(r["min"] or 0)
        a["gr"] += gr
        a["pts"] += num(r["pts"]); a["reb"] += num(r["reb"]); a["ast"] += num(r["ast"])
        a["w"] += 1 if r["won"] == "1" else 0
        if a["best"] is None or gr > a["best"][0]:
            a["best"] = (gr, r["date"], r["opp"], num(r["pts"]), r["url"])

    out = []
    for a in agg.values():
        gp = a["gp"]
        if gp < min_gp:
            continue
        # A traded player's denominator is the largest of his teams' game
        # counts, and the ratio is capped at 1: appearing in more games than any
        # one of his teams played is real, but it is not a bonus.
        denom = max((len(team_games[t]) for t in a["teams"]), default=0)
        ratio = min(gp / denom, 1.0) if denom else 0.0
        mean = a["gr"] / gp if gp else 0.0
        out.append({
            # season and section travel with the row, so a viewer reading several
            # of these files knows what it is looking at without parsing filenames.
            "season": label, "section": section,
            "player": a["player"], "player_id": a["player_id"],
            "player_url": a.get("player_url", ""),
            "league": a["league"], "team": "/".join(sorted(a["teams"])),
            "gp": gp, "team_gp": denom, "gp_ratio": round(ratio, 3),
            "min_pg": round(a["min"] / gp, 1),
            "pts_pg": round(a["pts"] / gp, 1), "reb_pg": round(a["reb"] / gp, 1),
            "ast_pg": round(a["ast"] / gp, 1),
            "win_pct": round(a["w"] / gp, 3),
            "mean_gr": round(mean, 2),
            "gr": round(mean * gp_mult(ratio), 2),
            "best_gr": round(a["best"][0], 2) if a["best"] else 0,
            "best_when": a["best"][1] if a["best"] else "",
            "best_vs": a["best"][2] if a["best"] else "",
            "best_pts": a["best"][3] if a["best"] else 0,
            "best_url": a["best"][4] if a["best"] else "",
        })
    # A player cannot appear in more games than the competition has dates. When
    # that happens the cache holds the same fixture twice under different ids,
    # which no dedupe on (player, date) can see -- so say so rather than publish
    # a total that cannot be right.
    all_dates = {r["date"] for r in rows if r.get("date")}
    over = [r for r in out if r["gp"] > len(all_dates)]
    if over:
        print(f"  !! {len(over)} player(s) with more games than the competition "
              f"has dates ({len(all_dates)}):")
        for r in over[:5]:
            print(f"     {r['player']} {r['gp']} GP")
        print("     the cache holds a fixture twice under different ids;"
              " --tidy will not fix this, a re-backfill will")

    out.sort(key=lambda r: -r["gr"])

    # The label comes from the years this competition WAS PLAYED IN, not the
    # years asked for. --years all covers 2024 to 2026, but an AmeriCup
    # qualifying cycle that finished in February 2025 is 2024-25, and labelling
    # it 2024-2026 claims a year of games that never happened.
    played_years = sorted({int(r["date"][:4]) for r in rows if r.get("date")})
    if played_years:
        label = span_label(played_years)
        for r in out:
            r["season"] = label

    lg = league_filter or list(leagues)[0]
    stem = re.sub(r"[^A-Za-z0-9]+", "-", f"{section}_{label}_{lg}").strip("-").lower()
    # Remove earlier files for this same section and league under a DIFFERENT
    # season label. The label moves when the years covered change -- adding 2024
    # to an AmeriCup cycle turns "2025-26" into "2024-2026" -- and the viewer
    # reads whatever it finds, so the old file becomes a duplicate of every team
    # and player rather than a harmless leftover.
    lgslug = re.sub(r"[^A-Za-z0-9]+", "-", lg).strip("-").lower()
    for pre in ("season_", "lastday_", "standings_"):
        for old_p in glob.glob(os.path.join(OUT, f"{pre}{section}-*-{lgslug}.csv")):
            if os.path.basename(old_p) != f"{pre}{stem}.csv":
                try:
                    os.remove(old_p)
                    print(f"  superseded: {os.path.basename(old_p)}")
                except OSError:
                    pass

    cpath = os.path.join(OUT, f"season_{stem}.csv")
    cols = ["season", "section", "player", "player_id", "player_url", "league", "team", "gp", "team_gp", "gp_ratio",
            "min_pg", "pts_pg", "reb_pg", "ast_pg", "win_pct", "mean_gr", "gr",
            "best_gr", "best_when", "best_vs", "best_pts", "best_url"]
    with open(cpath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(out)

    print(f"rgm_gr {VERSION}  (global_rating {GR_VERSION}, box-score part)")
    # Printed so the output identifies its own build. "Still 32" was reported
    # twice against a stale copy, and nothing on screen could distinguish that
    # from a fix that had not worked.
    print(f"  active: phase filter, id-isolation {ID_ISOLATION}, "
          f"team-name merge, date dedupe")
    print(f"  {lg} {label}: {len(rows)} line(s), {len(agg)} player(s), "
          f"{len(out)} qualified (min {min_gp} GP), GP_MODE={GP_MODE}")
    print(f"  phases in cache: " + ", ".join(f"{k}={v}" for k, v in have.most_common()))
    print(f"\n  {'#':>3}  {'PLAYER':<26} {'TEAM':<22} {'GP':>3} {'GR':>7} {'MEAN':>7} {'PTS':>5}")
    print("  " + "-" * 82)
    for i, r in enumerate(out[:25], 1):
        print(f"  {i:>3}  {r['player'][:25]:<26} {r['team'][:21]:<22} "
              f"{r['gp']:>3} {r['gr']:>7.2f} {r['mean_gr']:>7.2f} {r['pts_pg']:>5.1f}")
    print(f"\n  -> {cpath}")

    # STANDINGS, from the same rows.
    #
    # No extra fetching and no separate source: a game's result is already
    # implied by the lines -- both teams' points summed, and which side won. So
    # the table is derived rather than scraped, which also means it can never
    # disagree with the ratings sitting beside it.
    #
    # Whatever phase filter is in force applies here too, so a regular-season
    # table is a regular-season table and --phase playoffs gives a playoff record.
    tm = {}
    seen_g = {}
    for r in rows:
        g = r["game_id"]
        seen_g.setdefault(g, {})
        seen_g[g][r["team"]] = seen_g[g].get(r["team"], 0) + num(r["pts"])
    won_by = {}
    for r in rows:
        won_by.setdefault((r["game_id"], r["team"]), r["won"] == "1")
    for g, sides in seen_g.items():
        if len(sides) != 2:
            continue                     # a game with one side cached is not a result
        (ta, pa), (tb, pb) = list(sides.items())
        for me, mine, theirs in ((ta, pa, pb), (tb, pb, pa)):
            t = tm.setdefault(me, {"gp": 0, "w": 0, "l": 0, "pf": 0, "pa": 0})
            t["gp"] += 1
            t["pf"] += mine
            t["pa"] += theirs
            # The stored win flag decides, not the points: an abandoned or
            # forfeited game can have a winner the score does not show.
            if won_by.get((g, me), mine > theirs):
                t["w"] += 1
            else:
                t["l"] += 1
    if tm:
        st = []
        for name, t in tm.items():
            gp = t["gp"] or 1
            st.append({"team": name, "gp": t["gp"], "w": t["w"], "l": t["l"],
                       "pct": round(t["w"] / gp, 3),
                       "pf": t["pf"], "pa": t["pa"], "diff": t["pf"] - t["pa"],
                       "pf_pg": round(t["pf"] / gp, 1),
                       "pa_pg": round(t["pa"] / gp, 1),
                       "diff_pg": round((t["pf"] - t["pa"]) / gp, 1),
                       "league": lg, "season": label, "section": section})
        st.sort(key=lambda r: (-r["pct"], -r["diff"]))
        spath = os.path.join(OUT, f"standings_{stem}.csv")
        scols = ["season", "section", "league", "team", "gp", "w", "l", "pct",
                 "pf", "pa", "diff", "pf_pg", "pa_pg", "diff_pg"]
        with open(spath, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=scols, extrasaction="ignore")
            w.writeheader(); w.writerows(st)
        print(f"\n  STANDINGS  ({len(st)} team(s))")
        print(f"  {'#':>3}  {'TEAM':<26} {'GP':>3} {'W':>3} {'L':>3} {'PCT':>6} "
              f"{'PF':>6} {'PA':>6} {'DIFF':>6}")
        print("  " + "-" * 76)
        for i, r in enumerate(st[:16], 1):
            print(f"  {i:>3}  {r['team'][:25]:<26} {r['gp']:>3} {r['w']:>3} "
                  f"{r['l']:>3} {r['pct']:>6.3f} {r['pf_pg']:>6.1f} "
                  f"{r['pa_pg']:>6.1f} {r['diff_pg']:>+6.1f}")
        print(f"  -> {spath}")

    # The last day on record, which is not necessarily today: a game that has
    # tipped off but not finished is not in the cache yet, so the newest COMPLETE
    # date is the honest answer.
    if per_game:
        # Team scores, summed from the cached player lines. Points always belong
        # to a player -- unlike rebounds and turnovers, there is no "Team" row of
        # them -- so the sum is the score exactly, and no re-fetch is needed to
        # add a result column to a cache written before it existed.
        gscore = {}
        for r, _ in per_game:
            k = (r["game_id"], r["team"])
            gscore[k] = gscore.get(k, 0) + num(r["pts"])

        last = max(r["date"] for r, _ in per_game)
        day = sorted((x for x in per_game if x[0]["date"] == last),
                     key=lambda x: -x[1])
        dpath = os.path.join(OUT, f"lastday_{stem}.csv")
        dcols = ["date", "league", "player", "player_id", "player_url", "team", "opp", "won",
                 "min", "pts", "reb", "ast", "rating", "tm_score", "opp_score",
                 "result", "url"]
        with open(dpath, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=dcols, extrasaction="ignore")
            w.writeheader()
            for r, gr in day:
                a = gscore.get((r["game_id"], r["team"]))
                b = gscore.get((r["game_id"], r["opp"]))
                w.writerow({**r, "rating": round(gr, 2),
                            "tm_score": a if a is not None else "",
                            "opp_score": b if b is not None else "",
                            "result": ("W" if r["won"] == "1" else "L")})
        games = len({r["game_id"] for r, _ in day})
        print(f"\n  LAST DAY ON RECORD: {last}  ({games} game(s), {len(day)} line(s))")
        print(f"  {'#':>3}  {'PLAYER':<26} {'TEAM':<22} {'RATING':>7}  LINE")
        print("  " + "-" * 84)
        for i, (r, gr) in enumerate(day[:10], 1):
            a = gscore.get((r["game_id"], r["team"]))
            b = gscore.get((r["game_id"], r["opp"]))
            res = (f"  {'W' if r['won'] == '1' else 'L'} {a}-{b}"
                   if a is not None and b is not None else "")
            print(f"  {i:>3}  {r['player'][:25]:<26} {r['team'][:21]:<22} "
                  f"{gr:>7.2f}  {r['pts']}p {r['reb']}r {r['ast']}a{res}")
        print(f"  -> {dpath}")

    if want_html:
        hpath = os.path.join(OUT, f"season_{stem}.html")
        with open(hpath, "w", encoding="utf-8") as f:
            f.write(html_table(lg, label, section, out[:top], min_gp))
        print(f"  -> {hpath}")


def esc(x):
    return (str("" if x is None else x).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def html_table(league, year, section, rows, min_gp):
    """
    A viewer, deliberately plain. Sortable columns, a link to each player's best
    game, and no rating arithmetic anywhere in the page -- the numbers arrive
    already computed, which is the same contract the apps have with RAT 365.
    """
    head = [("#", ""), ("Player", "player"), ("Team", "team"), ("GP", "gp"),
            ("GR", "gr"), ("Mean", "mean_gr"), ("GP%", "gp_ratio"),
            ("Min", "min_pg"), ("Pts", "pts_pg"), ("Reb", "reb_pg"),
            ("Ast", "ast_pg"), ("Win%", "win_pct"), ("Best", "best_gr")]
    th = "".join(f'<th data-k="{k}">{esc(t)}</th>' for t, k in head)
    trs = []
    for i, r in enumerate(rows, 1):
        best = (f'<a href="{esc(r["best_url"])}" target="_blank" rel="noopener">'
                f'{r["best_gr"]:.1f}</a>' if r["best_url"] else f'{r["best_gr"]:.1f}')
        trs.append(
            f'<tr><td class="n">{i}</td><td class="p">{esc(r["player"])}</td>'
            f'<td class="t">{esc(r["team"])}</td><td>{r["gp"]}</td>'
            f'<td class="gr">{r["gr"]:.2f}</td><td>{r["mean_gr"]:.2f}</td>'
            f'<td>{r["gp_ratio"]:.2f}</td><td>{r["min_pg"]:.1f}</td>'
            f'<td>{r["pts_pg"]:.1f}</td><td>{r["reb_pg"]:.1f}</td>'
            f'<td>{r["ast_pg"]:.1f}</td><td>{r["win_pct"]:.3f}</td>'
            f'<td>{best}</td></tr>')
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(league)} {esc(str(year))} — Global Rating</title>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0b0b16;color:#e8e8f4;font-family:'JetBrains Mono',ui-monospace,monospace;padding:22px}}
h1{{font-family:'Oswald',sans-serif;font-size:26px;letter-spacing:.5px}}
h1 span{{color:#ffd24d}}
.sub{{color:#8a8ab8;font-size:12px;margin:6px 0 16px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th{{font-family:'Oswald',sans-serif;font-size:11px;letter-spacing:1px;text-transform:uppercase;
  color:#9a9ac8;text-align:right;padding:8px 7px;border-bottom:1px solid #2a2a52;cursor:pointer;white-space:nowrap}}
th:hover{{color:#ffd24d}}
th:nth-child(2),th:nth-child(3){{text-align:left}}
td{{padding:6px 7px;text-align:right;border-bottom:1px solid rgba(42,42,77,.5);white-space:nowrap}}
td.p,td.t{{text-align:left}}
td.n{{color:#6a6a94}}
td.p{{color:#fff}}
td.t{{color:#8a8ab8;font-size:12px}}
td.gr{{color:#ffd24d;font-weight:600}}
tbody tr:hover{{background:#14142b}}
a{{color:inherit;border-bottom:1px dotted rgba(255,255,255,.3);text-decoration:none}}
.foot{{color:#5c5c82;font-size:11px;margin-top:14px;line-height:1.6}}
</style></head><body>
<h1>{esc(league)} {esc(str(year))} — <span>Global Rating</span></h1>
<div class="sub">{len(rows)} players, minimum {min_gp} game{'' if min_gp==1 else 's'} ·
 rate x availability · click a header to sort · Best links to that box score</div>
<table><thead><tr>{th}</tr></thead><tbody>
{chr(10).join(trs)}
</tbody></table>
<div class="foot">
GR = mean rating per game x games played / team games (capped at 1).
Ratings are per possession, so game length and pace are already divided out --
but they are NOT comparable across leagues of different standard.
</div>
<script>
// Sort in place. Numbers descending first, names ascending, and the row numbers
// are left alone so they read as position in the current sort.
document.querySelectorAll('th[data-k]').forEach((th,ci)=>{{
  if(!th.dataset.k) return;
  let desc=true;
  th.onclick=()=>{{
    const tb=document.querySelector('tbody');
    const rows=[...tb.rows];
    const idx=[...th.parentNode.children].indexOf(th);
    const val=tr=>{{
      const t=tr.cells[idx].textContent.trim();
      const n=parseFloat(t.replace(/[^0-9.\\-]/g,''));
      return isNaN(n)?t.toLowerCase():n;
    }};
    rows.sort((a,b)=>{{
      const x=val(a),y=val(b);
      if(typeof x==='number'&&typeof y==='number') return desc?y-x:x-y;
      return desc?String(y).localeCompare(String(x)):String(x).localeCompare(String(y));
    }});
    desc=!desc;
    rows.forEach((r,i)=>{{r.cells[0].textContent=i+1;tb.appendChild(r);}});
  }};
}});
</script></body></html>"""


def arg(name, d=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return d


def main():
    section = (arg("--section", "wnba") or "").lower()
    if section not in RGM.SECTIONS:
        print(f"unknown section {section!r} -- choose from {', '.join(RGM.SECTIONS)}")
        return
    year = int(arg("--year", str(datetime.date.today().year)))
    if "--drop" in sys.argv:
        drop_leagues(section, year, arg("--drop", ""), "--dry" in sys.argv,
                     "--block" in sys.argv)
        return
    if "--realign" in sys.argv:
        align_added()
        for p in sorted(glob.glob(os.path.join(OUT, "lines_*.csv"))):
            n, d = align_cache(p)
            if not n:
                print(f"  {os.path.basename(p)}: already aligned ({d} rows)")
        return
    if "--urls" in sys.argv:
        i = sys.argv.index("--urls")
        items = [a for a in sys.argv[i + 1:] if not a.startswith("--")]
        if not items:
            print('  --urls needs a file of urls, or urls on the command line')
            return
        fetch_urls(items)
        return
    if "--coverage" in sys.argv:
        coverage(section, year, arg("--from") or f"{year}-01-01",
                 arg("--to") or f"{year}-12-31", "--fetch" in sys.argv,
                 arg("--match"))
        return
    if "--refetch" in sys.argv:
        refetch(arg("--refetch", ""),
                section if "--section" in sys.argv else None,
                "--dry" in sys.argv)
        return
    if "--tidy" in sys.argv:
        tidy("--dry" in sys.argv)
        return
    if "--auto" in sys.argv:
        auto("--force" in sys.argv)
        return
    if "--catchup" in sys.argv:
        catchup(section, int(arg("--days", "5")), year)
        return
    if "--prune" in sys.argv:
        prune(section, year, arg("--leagues"))
        return
    if "--backfill" in sys.argv:
        WANTED[0] = arg("--leagues")
        dfrom = arg("--from") or f"{year}-01-01"
        dto = arg("--to") or f"{year}-12-31"
        backfill(section, dfrom, dto, year)
        return
    if "--phases" in sys.argv:
        phases_report(section, year, int(arg("--per", "2")))
        return
    if "--ids" in sys.argv:
        ids_report(section, year, int(arg("--gap", "100")))
        return
    if "--inspect" in sys.argv:
        inspect(section, year, arg("--inspect", ""))
        return
    if "--who" in sys.argv:
        who(section, year, arg("--who", ""))
        return
    if "--season" in sys.argv:
        yv = arg("--years")
        yrs = None
        if yv and yv.strip().lower() == "all":
            # Every year on disk for this section: a qualifying cycle should not
            # need its years typing out.
            yrs = cached_years(section)
            if not yrs:
                print(f"no lines cache for {section} yet")
                return
        elif yv:
            yrs = sorted({int(x) for x in re.split(r"[,\s]+", yv) if x.strip().isdigit()})
        season(section, year, min_gp=int(arg("--min-gp", "1")),
               league_filter=arg("--league"), want_html="--html" in sys.argv,
               top=int(arg("--top", "200")), phase=arg("--phase"), years=yrs)
        return
    print(__doc__)


if __name__ == "__main__":
    main()
