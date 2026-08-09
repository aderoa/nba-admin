#!/usr/bin/env python3
"""
nba_changelog.py  -  Merge both NBA trackers' logs into one readable changelog.

Reads the two logs the trackers already produce:
  nba_auto_snapshots/changes_timeline.csv   (players table: ADDED / REMOVED / CHANGED)
  nba_tx_snapshots/transactions_log.csv     (new transactions)
and writes a single combined, chronological log:
  nba_changelog.csv   (detected_at, source, type, detail)
  nba_changelog.txt   (grouped by day, newest first, human-readable)

USAGE
-----
    python nba_changelog.py                 # everything, newest first
    python nba_changelog.py --asc           # oldest first
    python nba_changelog.py --days 7        # only the last 7 days
    python nba_changelog.py --since 2026-07-01
    python nba_changelog.py --source players     # players table only
    python nba_changelog.py --source transactions

Keep it in the same folder as the trackers (so it finds their snapshot folders).
"""

import os
import csv
import json
import sys
import argparse
import datetime
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
PLAYERS_CSV = os.path.join(BASE, "nba_auto_snapshots", "changes_timeline.csv")
TX_CSV = os.path.join(BASE, "nba_tx_snapshots", "transactions_log.csv")
DOCS_CSV = os.path.join(BASE, "nba_doc_snapshots", "docs_log.csv")
SHEETS_CSV = os.path.join(BASE, "nba_sheet_snapshots", "sheets_log.csv")
GLEAGUE_CSV = os.path.join(BASE, "nba_gleague_snapshots", "gleague_log.csv")
WIKI_CSV = os.path.join(BASE, "nba_wiki_snapshots", "wiki_log.csv")
RGM_CSV = os.path.join(BASE, "nba_rgm_snapshots", "rgm_log.csv")
RGM_STATE = os.path.join(BASE, "nba_rgm_snapshots", "rgm_state.json")
RGM_BEST = os.path.join(BASE, "nba_rgm_snapshots", "rgm_best.csv")
OUT_CSV = os.path.join(BASE, "nba_changelog.csv")
OUT_TXT = os.path.join(BASE, "nba_changelog.txt")
OUT_HTML = os.path.join(BASE, "nba_changelog.html")

import html as _html


def _kind(e):
    if e["source"] == "TRANSACTIONS":
        return "transaction"
    if e["source"] == "DOCS":
        return "doc"
    if e["source"] == "SHEETS":
        return "sheet"
    if e["source"] == "GLEAGUE":
        return "gleague"
    if e["source"] == "WIKI":
        return "wiki"
    if e["source"] == "REALGM":
        return "realgm"
    if e["source"] == "SCORES":
        return "scores"
    if e["source"] == "RATINGS":
        return "ratings"
    t = e["type"]
    if t == "ADDED":
        return "added"
    if t == "REMOVED":
        return "removed"
    if t in ("CHANGED", "TEAM_CHANGE"):
        return "changed"
    return "other"


def read_players():
    events = []
    if not os.path.exists(PLAYERS_CSV):
        return events
    with open(PLAYERS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            when = r.get("detected_at", "")
            typ = (r.get("type", "") or "").upper()
            name = r.get("name", "")
            if "field" in r:                                   # neutral schema
                fld, old, new = r.get("field", ""), r.get("old", ""), r.get("new", "")
            else:                                              # from_team/to_team schema
                fld, old, new = "team", r.get("from_team", ""), r.get("to_team", "")
            if typ == "ADDED":
                detail = f"+ ADDED   {name} ({new or 'no team'})"
            elif typ == "REMOVED":
                detail = f"- REMOVED {name} ({old or 'no team'})"
            elif typ in ("CHANGED", "TEAM_CHANGE"):
                detail = f"~ CHANGED {name}  {fld}: {old or '-'} -> {new or '-'}"
            else:
                detail = f"{typ} {name}".strip()
            events.append({"detected_at": when, "source": "PLAYERS", "type": typ, "detail": detail})
    return events


def read_gleague():
    events = []
    if not os.path.exists(GLEAGUE_CSV):
        return events
    with open(GLEAGUE_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            detail = (f"{r.get('date','')}  {r.get('description','')}").strip()
            events.append({"detected_at": r.get("detected_at", ""), "source": "GLEAGUE",
                           "type": r.get("type", ""), "detail": detail})
    return events


def _day_stamp(date):
    """
    A stable timestamp for something known only by its date.

    Three attempts, so the reasoning is worth keeping. 23:59:59 puts today's
    games in the FUTURE, and the clear cutoff is now, so they could never be
    cleared. Clamping to "now" was worse -- recomputed on every render, so the
    row was permanently newer than the cutoff. The file's mtime was worse again:
    appending a new rating moved it, resurrecting rows that had been cleared.

    So the stamp depends on NOTHING but the date. End of day for a past date,
    start of day for today -- both fixed, both in the past, both landing on the
    right day. Today's ratings sort to the top of today rather than the bottom,
    which is a small price for a row that can actually be dismissed.
    """
    if not date:
        return ""
    today = datetime.date.today().isoformat()
    return f"{date} 23:59:59" if date < today else f"{date} 00:00:01"


def read_ratings():
    """
    The best performances per game, from rgm_rating's output.

    Its own source rather than folded into the international feed: five per game
    across eight leagues is around seventy-five rows a day, which would bury a
    handful of transfers completely. As a separate source it has its own filter
    chip and can be switched off.

    Reads COMPUTED VALUES ONLY -- a rating, a name, a box-score line. The
    formula that produced the number is not here and must not be: this file
    lives in a public repo.
    """
    events = []
    if not os.path.exists(RGM_BEST):
        return events
    with open(RGM_BEST, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rating = float(r.get("rating") or 0)
            except ValueError:
                continue
            place = (r.get("place") or "").strip()
            star = "\u2605 " if place == "1" else ""
            line = (f"{r.get('pts','')}p {r.get('reb','')}r {r.get('ast','')}a "
                    f"{r.get('min','')}m").strip()
            detail = (f"{r.get('league','')}  {star}{place}. {r.get('player','')} "
                      f"{rating:.1f}  \u2014  {line}  ({r.get('team','')}"
                      f"{'' if r.get('won') == '1' else ', lost'})")
            events.append({
                # The game's own date, so a performance sits with the night it
                # happened rather than clumping wherever the rater ran -- but
                # CLAMPED TO NOW. Stamping today's games at 23:59:59 put them in
                # the future, and Clear sets its cutoff to the present, so those
                # rows survived every attempt to clear them until the day ended.
                "detected_at": (r.get("rated_at") or "").strip()
                               or _day_stamp(r.get("date", "")),
                "source": "RATINGS", "type": "TOP" + (place or ""),
                "detail": " ".join(detail.split()),
                "url": (r.get("url") or "").strip()})
    return events


def read_scores():
    """
    Final scores, from rgm_tracker's state file rather than a log.

    Its own source, not folded into REALGM, because results outnumber movement
    roughly three to one -- fifteen games a day across eight leagues against a
    handful of transfers -- and mixing them would make the movement impossible
    to see. As a separate source it gets its own filter chip and can simply be
    switched off.

    A game is read once and never again, so every score here is final.
    """
    events = []
    if not os.path.exists(RGM_STATE):
        return events
    try:
        with open(RGM_STATE, encoding="utf-8") as f:
            games = (json.load(f).get("games") or {})
    except (OSError, ValueError):
        return events
    for gid, g in games.items():
        sc = g.get("score") or []
        if len(sc) != 2 or None in sc:
            continue
        teams = (g.get("teams") or " / ").split(" / ")
        away = teams[0] if teams else "?"
        home = teams[1] if len(teams) > 1 else "?"
        # The winner first reads as a result rather than a fixture list.
        if sc[0] >= sc[1]:
            line = f"{away} {sc[0]}, {home} {sc[1]}"
        else:
            line = f"{home} {sc[1]}, {away} {sc[0]}"
        detail = f"{g.get('league','')}  {line}"
        events.append({"detected_at": g.get("read_at") or
                       _day_stamp(g.get("date", "")),
                       "source": "SCORES", "type": "FINAL",
                       "detail": " ".join(detail.split()),
                       "url": (g.get("url") or "").strip()})
    return events


def read_realgm():
    """
    International movement, from rgm_tracker's RealGM box scores.

    An appearance is the evidence: a player in a Mexican LNBP or PBA box score
    plays for that club. Only movement is logged -- SIGNED for a player new to
    the data, MOVED for a change of club, RETURN for a club he had left -- so
    this is not one row per game.
    """
    events = []
    if not os.path.exists(RGM_CSV):
        return events
    with open(RGM_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            typ = (r.get("type", "") or "").upper()
            frm = f" (from {r['from_team']})" if r.get("from_team") else ""
            mark = {"MOVED": "~ MOVED  ", "RETURN": "~ RETURN  "}.get(typ, "+ ")
            detail = (f"{r.get('league','')}  {mark}{r.get('player','')} "
                      f"\u2192 {r.get('team','')}{frm}")
            events.append({"detected_at": r.get("detected_at", ""),
                           "source": "REALGM", "type": typ,
                           "detail": " ".join(detail.split()),
                           "url": (r.get("url") or "").strip()})
    return events


def read_wiki():
    """
    Wikipedia infobox changes, from nba_wiki_tracker.

    Three types, and the marker says which at a glance: NEW is a player whose
    article did not exist before, CAREER is a stint added to the career
    history, and CHANGED is a plain infobox field moving -- team, league,
    number and the rest.
    """
    events = []
    if not os.path.exists(WIKI_CSV):
        return events
    with open(WIKI_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            typ = (r.get("type", "") or "").upper()
            mark = {"NEW": "+ NEW  ", "CAREER": "~ CAREER  "}.get(typ, "~ ")
            detail = f"{r.get('player','')}  {mark}{r.get('text','')}".strip()
            # Older rows predate the url column; derive it from the title so
            # every line links, not just the new ones.
            url = (r.get("url") or "").strip()
            if not url and r.get("player"):
                import urllib.parse
                url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(
                    r["player"].replace(" ", "_"), safe="_%()!,'-")
            events.append({"detected_at": r.get("detected_at", ""), "source": "WIKI",
                           "type": typ, "detail": detail, "url": url})
    return events


def read_sheets():
    events = []
    if not os.path.exists(SHEETS_CSV):
        return events
    with open(SHEETS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            typ = (r.get("type", "") or "").upper()
            mark = "+ ADDED  " if typ == "ADDED" else "- REMOVED " if typ == "REMOVED" else typ + " "
            detail = f"{r.get('sheet','')}  {mark}{r.get('text','')}"
            events.append({"detected_at": r.get("detected_at", ""), "source": "SHEETS",
                           "type": typ, "detail": detail})
    return events


def read_docs():
    events = []
    if not os.path.exists(DOCS_CSV):
        return events
    with open(DOCS_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            typ = (r.get("type", "") or "").upper()
            mark = "+ ADDED  " if typ == "ADDED" else "- REMOVED " if typ == "REMOVED" else typ + " "
            detail = f"{r.get('doc','')}  {mark}{r.get('text','')}"
            events.append({"detected_at": r.get("detected_at", ""), "source": "DOCS",
                           "type": typ, "detail": detail})
    return events


def read_tx():
    events = []
    if not os.path.exists(TX_CSV):
        return events
    with open(TX_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            when = r.get("detected_at", "")
            date = r.get("date", "")
            desc = r.get("description", "") or r.get("type", "")
            detail = (f"{date}  {desc}").strip()
            events.append({"detected_at": when, "source": "TRANSACTIONS",
                           "type": r.get("type", ""), "detail": detail})
    return events


def build_html(events, by_day, days, cutoff, live=False, hidden=0):
    def detail_html(e):
        """
        The detail, as a link when the event knows where it came from.

        rel=noopener and target=_blank because the report is a local page and
        the links go out to Wikipedia and RealGM. Anything without a url renders
        exactly as before, so the older rows are unaffected.
        """
        txt = esc(e["detail"])
        u = (e.get("url") or "").strip()
        if not u.startswith(("http://", "https://")):
            return txt
        return (f'<a class="src" href="{esc(u, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{txt}</a>')

    from collections import Counter
    kinds = Counter(_kind(e) for e in events)
    esc = _html.escape
    css = """
:root{--bg:#0b0b16;--panel:#15152a;--panel2:#1c1c38;--line:#28284a;--tx:#e8e8f4;--dim:#8a8ab0;
--green:#34d97a;--red:#ff6b5a;--amber:#ffd24d;--blue:#5aa9ff;--purple:#b98aff;--teal:#3ad6c6;--coral:#ff8a5c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:-apple-system,Segoe UI,Roboto,Helvetica,sans-serif;font-size:14px;padding:0 0 60px}
.wrap{max-width:900px;margin:0 auto;padding:24px 16px}
h1{font-size:23px;letter-spacing:.3px}
.sub{color:var(--dim);margin:4px 0 16px;font-size:13px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:6px 14px;font-size:12px;color:var(--dim)}
.chip b{color:var(--tx);font-size:14px}
.chip.added b{color:var(--green)}.chip.removed b{color:var(--red)}.chip.changed b{color:var(--amber)}.chip.transaction b{color:var(--blue)}.chip.doc b{color:var(--purple)}.chip.sheet b{color:var(--teal)}.chip.gleague b{color:var(--coral)}.chip.wiki b{color:#7fd3ff}.chip.realgm b{color:#8ef0a8}.chip.scores b{color:#f0d78e}.chip.ratings b{color:#c9a2ff}
button.danger{border-color:#5a2a3a;color:#ff9db1}
button.danger:hover{border-color:#8a3a52;color:#ffc2ce}
.detail a.src{color:inherit;text-decoration:none;border-bottom:1px dotted rgba(255,255,255,.28)}
.detail a.src:hover{border-bottom-color:currentColor}
.bar{position:sticky;top:0;background:var(--bg);padding:12px 0;display:flex;gap:8px;flex-wrap:wrap;
border-bottom:1px solid var(--line);margin-bottom:6px;z-index:5}
.bar select,.bar input{background:var(--panel);border:1px solid var(--line);border-radius:8px;color:var(--tx);
padding:8px 11px;font-size:13px;font-family:inherit}
.bar input{flex:1;min-width:180px}.bar input:focus,.bar select:focus{outline:none;border-color:var(--blue)}
.bar button{background:var(--blue);color:#08131f;border:none;border-radius:8px;padding:8px 14px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}
.bar button:hover{filter:brightness(1.1)}
.live{color:var(--green);font-size:11px;margin-left:6px}
.day{margin-top:20px}
.day h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:1.5px;
border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:4px;display:flex;justify-content:space-between}
.day h2 .dc{font-weight:400}
.ev{display:flex;gap:11px;align-items:baseline;padding:8px 10px;border-left:3px solid var(--line);
border-radius:0 8px 8px 0;margin:2px 0}
.ev:hover{background:var(--panel)}
.ev .time{color:var(--dim);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;min-width:62px;flex:none}
.ev .badge{font-size:9px;letter-spacing:1px;border:1px solid var(--line);border-radius:5px;padding:2px 6px;color:var(--dim);flex:none}
.ev .detail{flex:1;line-height:1.4}
.ev.added{border-left-color:var(--green)} .ev.removed{border-left-color:var(--red)}
.ev.doc{border-left-color:var(--purple)}
.ev.sheet{border-left-color:var(--teal)}
.ev.gleague{border-left-color:var(--coral)}
.ev.wiki{border-left-color:#7fd3ff}
.ev.realgm{border-left-color:#8ef0a8}
.ev.scores{border-left-color:#f0d78e}
.ev.ratings{border-left-color:#c9a2ff}
.ev.changed{border-left-color:var(--amber)} .ev.transaction{border-left-color:var(--blue)}
.ev.added .detail{color:#bff0d3}.ev.removed .detail{color:#ffd0c8}
.empty{color:var(--dim);text-align:center;padding:40px}
.hidden{display:none!important}
"""
    js = """
const $=s=>document.querySelector(s), evs=[...document.querySelectorAll('.ev')];
function apply(){
  const src=$('#fSource').value, kind=$('#fKind').value, q=$('#fSearch').value.trim().toLowerCase();
  for(const e of evs){
    const ok=(src==='all'||e.dataset.source===src)&&(kind==='all'||e.dataset.kind===kind)
      &&(!q||e.dataset.search.includes(q));
    e.classList.toggle('hidden',!ok);
  }
  let shown=0;
  for(const d of document.querySelectorAll('.day')){
    const vis=[...d.querySelectorAll('.ev')].filter(e=>!e.classList.contains('hidden')).length;
    d.classList.toggle('hidden',vis===0); shown+=vis;
    const c=d.querySelector('.dc'); if(c)c.textContent=vis;
  }
  $('#count').textContent=shown+' shown';
  $('#none').classList.toggle('hidden',shown>0);
}
['#fSource','#fKind','#fSearch'].forEach(s=>$(s).addEventListener('input',apply));
"""
    parts = []
    for d in days:
        parts.append(f'<div class="day"><h2><span>{esc(d)}</span><span class="dc">{len(by_day[d])}</span></h2>')
        for e in by_day[d]:
            k = _kind(e)
            t = esc(e["detected_at"][11:19])
            badge = {"PLAYERS": "PLAYERS", "TRANSACTIONS": "TRANSACTION", "DOCS": "DOC", "SHEETS": "SHEET", "GLEAGUE": "G LEAGUE", "WIKI": "WIKIPEDIA",
                 "REALGM": "INTERNATIONAL",
                 "SCORES": "FINAL SCORE",
                 "RATINGS": "TOP RATED"}.get(e["source"], e["source"])
            search = esc((e["detail"] + " " + e["source"]).lower(), quote=True)
            parts.append(
                f'<div class="ev {k}" data-source="{e["source"]}" data-kind="{k}" data-search="{search}">'
                f'<span class="time">{t}</span><span class="badge">{badge}</span>'
                f'<span class="detail">{detail_html(e)}</span></div>')
        parts.append('</div>')
    body = "\n".join(parts)

    def chip(label, key, cls=""):
        return f'<span class="chip {cls}">{label} <b>{kinds.get(key, 0)}</b></span>'

    gen = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    n_events = len(events)
    live_flag = "true" if live else "false"
    # Clear marks a cutoff; it does NOT delete anything. The logs are the record
    # and the trackers keep appending -- this only stops showing what is already
    # read. So it is reversible, which is why "Show all" sits next to it and why
    # the confirm text says hidden rather than deleted.
    # With nothing to show, keep the page rather than replacing it with a bare
    # message: the header, the chips and the buttons are how you get back, and a
    # stripped-down page loses the Show all control just when it is needed.
    empty_state = ""
    if not events:
        empty_state = ('<div class="empty">'
                       + ("Nothing new since you cleared."
                          if cutoff else "No changes logged yet.")
                       + " New entries appear here as the trackers catch them."
                       + "</div>")
    clear_btn = ""
    if live:
        clear_btn = ('<button id="clearbtn" class="danger" '
                     'onclick="doClear()">\u2715 Clear</button>')
        if hidden:
            clear_btn += (f'<button id="showall" onclick="location=\'/showall\'">'
                          f'\u21ba Show all ({hidden} hidden)</button>')
    html_doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NBA Change Log</title><style>{css}</style></head><body><div class="wrap">
<h1>NBA Change Log</h1>
<div class="sub">Generated {gen} &middot; {len(events)} change(s) across {len(days)} day(s)"""
    if cutoff:
        html_doc += f" &middot; since {esc(cutoff)}"
    if live:
        html_doc += ' <span class="live">\u25cf live &mdash; rebuilds on each load</span>'
    html_doc += f"""</div>
<div class="chips">
  <span class="chip"><b>{len(events)}</b> total</span>
  {chip("Added","added","added")}{chip("Removed","removed","removed")}
  {chip("Changed","changed","changed")}{chip("Transactions","transaction","transaction")}{chip("Docs","doc","doc")}{chip("Sheets","sheet","sheet")}{chip("G League","gleague","gleague")}{chip("Wikipedia","wiki","wiki")}{chip("International","realgm","realgm")}{chip("Scores","scores","scores")}{chip("Top rated","ratings","ratings")}
</div>
<div class="bar">
  <select id="fSource"><option value="all">All sources</option>
    <option value="PLAYERS">Players table</option><option value="TRANSACTIONS">Transactions</option><option value="DOCS">Docs</option><option value="SHEETS">Sheets</option><option value="GLEAGUE">G League</option></select>
  <select id="fKind"><option value="all">All types</option>
    <option value="added">Added</option><option value="removed">Removed</option>
    <option value="changed">Changed</option><option value="transaction">Transactions</option><option value="doc">Docs</option><option value="sheet">Sheets</option><option value="gleague">G League</option></select>
  <input id="fSearch" type="text" placeholder="search player / text\u2026" autocomplete="off">
  {'<button id="reload" onclick="location.reload()">\u21bb Update</button>' if live else ''}
  {clear_btn}
  <span class="chip" id="count">{len(events)} shown</span>
</div>
{body}
{empty_state}
<div class="empty hidden" id="none">No changes match your filters.</div>
</div><script>{js}
/* The tab title carries the count of entries that have arrived since this page
   was drawn -- "NBA Change Log (3)" -- so an open tab is worth glancing at
   without refreshing it. Only in the live server: the static export has nothing
   to poll, and a title that never changes would be a lie. */
(function(){{
  var RENDERED = {n_events};
  var BASE = document.title;
  var timer = null;
  function tick(){{
    fetch('/count', {{cache:'no-store'}})
      .then(function(r){{ return r.ok ? r.json() : null; }})
      .then(function(j){{
        if(!j || j.n < 0) return;
        var extra = j.n - RENDERED;
        // Negative means entries were cleared elsewhere, not that time ran
        // backwards -- treat it as nothing new rather than showing "(-4)".
        document.title = extra > 0 ? '(' + extra + ') ' + BASE : BASE;
        var b = document.getElementById('reload');
        if(b) b.textContent = extra > 0
          ? '\u21bb Update \u2014 ' + extra + ' new' : '\u21bb Update';
      }})
      .catch(function(){{}});
  }}
  if({live_flag}){{
    tick();
    timer = setInterval(tick, 45000);
    // A hidden tab does not need polling, and a returning one wants the answer
    // immediately rather than up to 45 seconds later.
    document.addEventListener('visibilitychange', function(){{
      if(document.visibilityState === 'visible') tick();
    }});
  }}
}})();

function doClear(){{
  var n = document.querySelectorAll('.ev').length;
  if(!confirm('Hide the ' + n + ' entries currently shown?\\n\\n'
    + 'Nothing is deleted -- the logs keep every row and new changes will still '
    + 'appear. You can undo this with "Show all".')) return;
  location = '/clear';
}}
</script></body></html>"""
    return html_doc


def write_html(events, by_day, days, cutoff):
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(events, by_day, days, cutoff, live=False))


def collect_all():
    """All feeds merged, newest first not applied here (caller sorts). Used by the live server."""
    return (read_players() + read_tx() + read_docs() + read_sheets()
            + read_gleague() + read_wiki() + read_realgm() + read_scores()
            + read_ratings())


def main():
    ap = argparse.ArgumentParser(description="Merge both NBA tracker logs into one changelog.")
    ap.add_argument("--asc", action="store_true", help="oldest first (default: newest first)")
    ap.add_argument("--days", type=int, help="only the last N days")
    ap.add_argument("--since", help="only changes on/after this date (YYYY-MM-DD)")
    ap.add_argument("--source", choices=["players", "transactions", "docs", "sheets", "gleague", "wiki", "realgm", "scores", "ratings", "all"], default="all")
    args = ap.parse_args()

    events = []
    if args.source in ("players", "all"):
        events += read_players()
    if args.source in ("transactions", "all"):
        events += read_tx()
    if args.source in ("docs", "all"):
        events += read_docs()
    if args.source in ("sheets", "all"):
        events += read_sheets()
    if args.source in ("gleague", "all"):
        events += read_gleague()
    if args.source in ("wiki", "all"):
        events += read_wiki()
    if args.source in ("realgm", "all"):
        events += read_realgm()
    if args.source in ("scores", "all"):
        events += read_scores()
    if args.source in ("ratings", "all"):
        events += read_ratings()

    # date filtering
    cutoff = None
    if args.since:
        cutoff = args.since
    elif args.days is not None:
        cutoff = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
    if cutoff:
        events = [e for e in events if e["detected_at"][:10] >= cutoff]

    if not events:
        print("No changes found. Have the trackers run yet, and are the snapshot folders here?")
        print(f"  looked for: {PLAYERS_CSV}\n              {TX_CSV}")
        return

    events.sort(key=lambda e: e["detected_at"], reverse=not args.asc)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["detected_at", "source", "type", "detail"])
        for e in events:
            w.writerow([e["detected_at"], e["source"], e["type"], e["detail"]])

    by_day = defaultdict(list)
    for e in events:
        by_day[e["detected_at"][:10]].append(e)
    days = sorted(by_day, reverse=not args.asc)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(f"NBA CHANGE LOG  -  generated {datetime.datetime.now():%Y-%m-%d %H:%M}\n")
        f.write(f"{len(events)} change(s) across {len(days)} day(s)"
                + (f", since {cutoff}" if cutoff else "") + "\n")
        for d in days:
            f.write(f"\n=== {d} ===\n")
            for e in by_day[d]:
                t = e["detected_at"][11:19]
                f.write(f"  {t}  [{e['source'][:2]}] {e['detail']}\n")

    write_html(events, by_day, days, cutoff)

    print(f"Wrote:\n  {OUT_HTML}\n  {OUT_TXT}\n  {OUT_CSV}\n{len(events)} change(s) across {len(days)} day(s).")


if __name__ == "__main__":
    main()
