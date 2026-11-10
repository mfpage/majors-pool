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
HTML_URL = "https://www.espn.com/golf/leaderboard?tournamentId={event_id}"

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
    rounds: list = field(default_factory=lambda: [None, None, None, None])  # strokes per round
    total: int | None = None   # total strokes


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
def find_majors(year: int, overrides: dict | None = None, tests: list | None = None) -> dict[str, Event]:
    """Return the four majors for `year`, keyed by major key, plus any test
    tournaments (keyed "test-...") whose ESPN name contains one of `tests`.

    `overrides` maps major key -> ESPN event id, for when ESPN's schedule
    can't be read or names a major differently.
    """
    overrides = overrides or {}
    tests = [t.lower() for t in (tests or []) if t]
    events: dict[str, Event] = {}
    try:
        data = _get(SCHEDULE_URL, params={"dates": year}).json()
        calendar = (data.get("leagues") or [{}])[0].get("calendar") or []
        for item in calendar:
            label = (item.get("label") or "").strip()
            for t in tests:
                if t in label.lower():
                    key = "test-" + re.sub(r"[^a-z0-9]+", "-", t).strip("-")
                    events.setdefault(key, Event(key, label, str(item.get("id")),
                                                 _parse_date(item.get("startDate")),
                                                 _parse_date(item.get("endDate"))))
            for key, (name, pattern) in MAJORS.items():
                if key not in events and pattern.match(label):
                    events[key] = Event(
                        key, name, str(item.get("id")),
                        _parse_date(item.get("startDate")),
                        _parse_date(item.get("endDate")),
                    )
    except Exception as exc:  # schedule is best effort; overrides cover it
        print(f"[schedule] could not read ESPN schedule: {exc}")

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
    for source, fn in (("html", fetch_html), ("feed", fetch_feed), ("feed-alt", fetch_feed_alt)):
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
def fetch_feed(event_id: str) -> list[Row]:
    data = _get(FEED_EVENT_URL.format(event_id=event_id)).json()
    comps = data.get("competitions") or []
    if not comps:
        raise FetchError("no competitions in feed")
    return [parse_feed_competitor(c) for c in comps[0].get("competitors") or []]


def fetch_feed_alt(event_id: str) -> list[Row]:
    data = _get(FEED_LEADERBOARD_URL, params={"league": "pga", "event": event_id}).json()
    events = data.get("events") or []
    if not events:
        raise FetchError("no events in leaderboard feed")
    comps = events[0].get("competitions") or []
    if not comps:
        raise FetchError("no competitions in leaderboard feed")
    return [parse_feed_competitor(c) for c in comps[0].get("competitors") or []]


def _text(v):
    if isinstance(v, dict):
        return str(v.get("displayValue") or v.get("value") or "")
    return "" if v is None else str(v)


def _feed_thru(status: dict, holes_this_round) -> str:
    """Turn the feed's status into the table's THRU value: F, a hole, or a tee time."""
    raw = ""
    for key in ("thru", "displayValue", "detail"):
        v = status.get(key)
        if v not in (None, ""):
            raw = str(v).strip()
            break
    tee = re.search(r"\d{1,2}:\d{2}\s*[AP]M", raw, re.I)
    if tee:
        return tee.group(0).upper()
    if raw.upper().startswith("F"):
        return "F"
    hole = re.search(r"\d+", raw)
    if hole:
        n = int(hole.group(0))
        return "F" if n >= 18 else (str(n) if n > 0 else "")
    if holes_this_round:
        return "F" if holes_this_round >= 18 else str(holes_this_round)
    return ""


def parse_feed_competitor(c: dict) -> Row:
    athlete = c.get("athlete") or {}
    name = athlete.get("displayName") or athlete.get("fullName") or ""
    status = c.get("status") or {}
    stype = status.get("type") or {}
    status_words = " ".join(str(stype.get(k, "")) for k in ("name", "description", "shortDetail", "state")).upper()

    score = _text(c.get("score"))
    out = ""
    if score.upper() in OUT_STATUSES:
        out = score.upper()
    elif "CUT" in status_words:
        out = "CUT"
    elif "WITHDR" in status_words or re.search(r"\bWD\b", status_words):
        out = "WD"
    elif "DISQUAL" in status_words or re.search(r"\bDQ\b", status_words):
        out = "DQ"

    # Round strokes: a round counts once all 18 holes are in, when the feed
    # gives hole-by-hole detail; otherwise rounds before the current one count.
    current_period = status.get("period")
    rounds = [None, None, None, None]
    holes_this_round = None
    for ls in c.get("linescores") or []:
        p = ls.get("period")
        if not isinstance(p, int) or not 1 <= p <= 4:
            continue
        holes = ls.get("linescores")
        val = ls.get("value")
        if isinstance(holes, list) and holes:
            if p == current_period or holes_this_round is None:
                holes_this_round = len(holes)
            complete = len(holes) >= 18
        else:
            complete = current_period is None or p < current_period
        if complete and isinstance(val, (int, float)) and val > 0:
            rounds[p - 1] = int(val)

    thru = _feed_thru(status, holes_this_round)

    pos = ""
    pos_obj = status.get("position") or {}
    if isinstance(pos_obj, dict):
        pos = str(pos_obj.get("displayName") or "")
    if not pos and c.get("order") is not None:
        pos = str(c.get("order"))

    return Row(
        name=name, pos=pos, score="" if out else score,
        thru="F" if out else thru.replace("*", ""), out=out, rounds=rounds,
        total=sum(r for r in rounds if r) or None,
    )
