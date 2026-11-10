"""Fetching from ESPN.

Two independent ways of reading a leaderboard are tried in order; the first
one that returns a sensible field wins:

1. "html"  - the leaderboard table on espn.com (same approach as the original
             R script, which worked through the 2026 season).
2. "feed"  - ESPN's JSON data feed that its own apps use.

Both produce the same list of Row objects, so nothing downstream cares which
one worked. The schedule (to find each major's event id) comes from the feed,
with config.json overrides as a manual fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30

SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
FEED_EVENT_URL = "https://site.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard/{event_id}"
FEED_LEADERBOARD_URL = "https://site.web.api.espn.com/apis/site/v2/sports/golf/leaderboard"
HTML_URL = "https://www.espn.com/golf/leaderboard/_/tournamentId/{event_id}"

OUT_STATUSES = ("CUT", "WD", "DQ", "DNS", "DNP")

# Major key -> (display name used in file names, pattern for ESPN's label)
MAJORS = {
    "masters": ("Masters", re.compile(r"^(the )?masters( tournament)?$", re.I)),
    "pga": ("PGA Championship", re.compile(r"^pga championship$", re.I)),
    "usopen": ("US Open", re.compile(r"^u\.?\s?s\.? open( championship)?$", re.I)),
    "open": ("British Open", re.compile(r"^(the open( championship)?|british open)$", re.I)),
}


@dataclass
class Row:
    """One golfer on the leaderboard, in the same terms as the ESPN table."""
    name: str
    pos: str = ""
    score: str = ""            # to par as shown: "-5", "E", "+2", "" before start
    thru: str = ""             # "F", "12", "1:44 PM", ""
    out: str = ""              # one of OUT_STATUSES, or ""
    rounds: list = field(default_factory=lambda: [None, None, None, None])  # strokes per finished round
    total: int | None = None   # total strokes
    round_to_par: list = field(default_factory=lambda: [None, None, None, None])  # per finished round, if known


@dataclass
class Event:
    key: str
    name: str
    event_id: str
    start: date | None
    end: date | None


class FetchError(Exception):
    pass


def _get(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
def calendar(year: int) -> list[tuple[str, str, date | None, date | None]]:
    """Every tournament ESPN lists for `year`, the next season, and this week,
    as (name, event id, start, end). Duplicates removed."""
    seen, out = set(), []

    def add(label, eid, start, end):
        if eid and eid not in seen:
            seen.add(eid)
            out.append(((label or "").strip(), str(eid), _parse_date(start), _parse_date(end)))

    for y in (year, year + 1):
        try:
            data = _get(SCHEDULE_URL, params={"dates": y}).json()
            for item in (data.get("leagues") or [{}])[0].get("calendar") or []:
                add(item.get("label"), item.get("id"), item.get("startDate"), item.get("endDate"))
        except Exception as exc:
            print(f"[schedule] could not read ESPN's {y} schedule: {exc}")
    try:  # this week's tournament, straight from the live scoreboard
        data = _get(SCHEDULE_URL).json()
        for ev in data.get("events") or []:
            add(ev.get("name") or ev.get("shortName"), ev.get("id"), ev.get("date"), ev.get("endDate"))
    except Exception as exc:
        print(f"[schedule] could not read this week's scoreboard: {exc}")
    return out


def find_majors(year: int, overrides: dict | None = None, tests: list | None = None) -> dict[str, Event]:
    """Return the four majors for `year`, keyed by major key, plus any test
    tournaments (keyed "test-...") whose ESPN name contains one of `tests`.

    `overrides` maps major key -> ESPN event id, for when ESPN's schedule
    can't be read or names a major differently.
    """
    overrides = overrides or {}
    tests = [t.lower() for t in (tests or []) if t]
    events: dict[str, Event] = {}
    for label, eid, start, end in calendar(year):
        if start and start.year != year:
            continue  # next season's calendar is only searched for this year's dates
        for t in tests:
            if t in label.lower():
                key = "test-" + re.sub(r"[^a-z0-9]+", "-", t).strip("-")
                events.setdefault(key, Event(key, label, eid, start, end))
        for key, (name, pattern) in MAJORS.items():
            if key not in events and pattern.match(label):
                events[key] = Event(key, name, eid, start, end)

    for key, event_id in overrides.items():
        if key in MAJORS and event_id:
            name = MAJORS[key][0]
            prior = events.get(key)
            events[key] = Event(key, name, str(event_id),
                                prior.start if prior else None,
                                prior.end if prior else None)
    return events


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------
def fetch_leaderboard(event_id: str) -> tuple[list[Row], str]:
    """Return (rows, source_name). Raises FetchError if every source fails."""
    problems = []
    for source, fn in (("leaderboard feed", fetch_leaderboard_feed), ("leaderboard page", fetch_html),
                       ("scoreboard feed", fetch_feed)):
        try:
            rows = fn(event_id)
            ok, why = looks_valid(rows)
            if ok:
                return rows, source
            problems.append(f"{source}: {why}")
        except Exception as exc:
            problems.append(f"{source}: {type(exc).__name__}: {exc}")
    raise FetchError("; ".join(problems))


def looks_valid(rows: list[Row]) -> tuple[bool, str]:
    if len(rows) < 30:
        return False, f"only {len(rows)} golfers found"
    named = sum(1 for r in rows if r.name and len(r.name) > 3)
    if named < 0.9 * len(rows):
        return False, "names missing"
    return True, ""


# ---- Source 1: the espn.com leaderboard table ----------------------------
def fetch_html(event_id: str) -> list[Row]:
    return parse_html(_get(HTML_URL.format(event_id=event_id)).text)


def parse_html(html: str) -> list[Row]:
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for t in soup.find_all("table"):
        if t.find("tr", class_=re.compile(r"PlayerRow__Overview")):
            table = t
            break
    if table is None:
        raise FetchError("leaderboard table not found on page")

    head = table.find("thead")
    header = [c.get_text(" ", strip=True).upper() for c in head.find_all(["th", "td"])] if head else []

    rows = []
    for tr in table.find_all("tr", class_=re.compile(r"PlayerRow__Overview")):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        # ESPN sometimes has one extra leading cell (the expand arrow)
        hdr = header
        if len(cells) == len(header) + 1:
            cells = cells[1:]
        elif len(header) == len(cells) + 1:
            hdr = header[1:]
        rec = dict(zip(hdr, cells))
        name = rec.get("PLAYER", "").strip()
        if not name:
            continue
        rows.append(_row_from_columns(
            name=name,
            pos=rec.get("POS", ""),
            score=rec.get("SCORE", ""),
            thru=rec.get("THRU", "F" if "THRU" not in rec else ""),
            r=[rec.get(f"R{i}") for i in range(1, 5)],
            tot=rec.get("TOT"),
        ))
    return rows


def _row_from_columns(name, pos, score, thru, r, tot) -> Row:
    score = (score or "").strip()
    pos = (pos or "").strip()
    out = ""
    for cand in (score.upper(), pos.upper()):
        if cand in OUT_STATUSES:
            out = cand
    return Row(
        name=name,
        pos=pos,
        score="" if out else score,
        thru="F" if out else (thru or "").replace("*", "").strip(),
        out=out,
        rounds=[_int(x) for x in r],
        total=_int(tot),
    )


def _int(x):
    if x is None:
        return None
    x = str(x).strip()
    return int(x) if re.fullmatch(r"\d{2,3}", x) else None


# ---- Source 2 and 3: ESPN's JSON feed ------------------------------------
def fetch_leaderboard_feed(event_id: str) -> list[Row]:
    """ESPN's leaderboard feed: the same data as the leaderboard on espn.com,
    with each golfer's status (finished, cut, withdrawn), hole and round."""
    data = _get(FEED_LEADERBOARD_URL, params={"league": "pga", "event": event_id}).json()
    return parse_leaderboard_feed(data)


def parse_leaderboard_feed(data: dict) -> list[Row]:
    events = data.get("events") or []
    if not events:
        raise FetchError("no events in leaderboard feed")
    comps = events[0].get("competitions") or []
    if not comps:
        raise FetchError("no competitions in leaderboard feed")
    return [_leaderboard_row(c) for c in comps[0].get("competitors") or []]


def _tee_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York"))
        return t.strftime("%-I:%M %p")
    except Exception:
        return ""


def _out_status(stype: dict, display: str) -> str:
    words = " ".join(str(stype.get(k, "")) for k in ("name", "description", "shortDetail")).upper()
    display = (display or "").upper()
    if display in OUT_STATUSES:
        return display
    if "CUT" in words and "MADE" not in words:
        return "CUT"
    if "WITHDR" in words or re.search(r"\bWD\b", words):
        return "WD"
    if "DISQUAL" in words or re.search(r"\bDQ\b", words):
        return "DQ"
    if "DID NOT START" in words or re.search(r"\bDNS\b", words):
        return "DNS"
    return ""


def _leaderboard_row(c: dict) -> Row:
    athlete = c.get("athlete") or {}
    name = athlete.get("displayName") or athlete.get("fullName") or ""
    status = c.get("status") or {}
    stype = status.get("type") or {}
    display = str(status.get("displayValue") or "")
    out = _out_status(stype, display)

    period = status.get("period") if isinstance(status.get("period"), int) else None
    thru = status.get("thru")
    thru = thru if isinstance(thru, int) else None
    finished_now = thru == 18 or stype.get("name") == "STATUS_FINISH" or display.upper() == "F"

    rounds, to_par = [None] * 4, [None] * 4
    for ls in c.get("linescores") or []:
        p = ls.get("period")
        val = ls.get("value")
        if not isinstance(p, int) or not 1 <= p <= 4 or not isinstance(val, (int, float)) or val <= 0:
            continue
        complete = period is None or p < period or (p == period and finished_now)
        if complete:
            rounds[p - 1] = int(val)
            rp = ls.get("displayValue")
            to_par[p - 1] = rp if isinstance(rp, str) else None

    if out:
        shown = "F"
    elif finished_now:
        shown = "F"
    elif thru and 0 < thru < 18:
        shown = str(thru)
    else:
        shown = _tee_time(status.get("teeTime")) or ""

    pos = status.get("position") or {}
    return Row(
        name=name,
        pos=str(pos.get("displayName") or "") if isinstance(pos, dict) else "",
        score="" if out else _text(c.get("score")),
        thru=shown,
        out=out,
        rounds=rounds,
        total=sum(r for r in rounds if r) or None,
        round_to_par=to_par,
    )


def fetch_feed(event_id: str) -> list[Row]:
    """ESPN's scoreboard feed: a last resort. It has scores and rounds but no
    status, so cut golfers are inferred from the rounds they didn't play."""
    data = _get(FEED_EVENT_URL.format(event_id=event_id)).json()
    comps = data.get("competitions") or []
    if not comps:
        raise FetchError("no competitions in feed")
    rows = []
    for c in comps[0].get("competitors") or []:
        athlete = c.get("athlete") or {}
        rounds, to_par = [None] * 4, [None] * 4
        for ls in c.get("linescores") or []:
            p, val, holes = ls.get("period"), ls.get("value"), ls.get("linescores")
            if isinstance(p, int) and 1 <= p <= 4 and isinstance(val, (int, float)) and val > 0 \
                    and isinstance(holes, list) and len(holes) >= 18:
                rounds[p - 1] = int(val)
                to_par[p - 1] = ls.get("displayValue")
        rows.append(Row(name=athlete.get("displayName") or "", pos=str(c.get("order") or ""),
                        score=_text(c.get("score")), thru="F", rounds=rounds,
                        total=sum(r for r in rounds if r) or None, round_to_par=to_par))
    if any(r.rounds[2] for r in rows):  # round 3 played: anyone who stopped after 2 was cut
        for r in rows:
            if r.rounds[0] and r.rounds[1] and not r.rounds[2]:
                r.out, r.score = "CUT", ""
    return rows


def fetch_feed_alt(event_id: str) -> list[Row]:  # kept for the probe command
    return fetch_leaderboard_feed(event_id)


def _text(v):
    if isinstance(v, dict):
        return str(v.get("displayValue") or v.get("value") or "")
    return "" if v is None else str(v)
