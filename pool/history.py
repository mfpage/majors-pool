"""One-time source for majors ESPN doesn't have (1999 and 2000): leaderboards
saved from europeantour.com into the history/ folder.

How to save one: open the tournament's leaderboard on europeantour.com at full
width, open Chrome's inspector undocked into its own window, and in the
Console run
    copy(document.querySelector("table.VueTables__table").outerHTML)
then paste into a file named like "history/2000 PGA Championship.html"
(year, then Masters, PGA Championship, US Open or British Open).

Backfill reads these files first, then falls back to Wikipedia.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from .espn import Row

# Course details for the seasons ESPN doesn't cover
VENUES = {
    (1999, "masters"): ("Augusta National Golf Club", "Augusta, GA", date(1999, 4, 8), date(1999, 4, 11)),
    (1999, "usopen"): ("Pinehurst Resort, No. 2", "Pinehurst, NC", date(1999, 6, 17), date(1999, 6, 20)),
    (1999, "open"): ("Carnoustie Golf Links", "Carnoustie, Scotland", date(1999, 7, 15), date(1999, 7, 18)),
    (1999, "pga"): ("Medinah Country Club", "Medinah, IL", date(1999, 8, 12), date(1999, 8, 15)),
    (2000, "masters"): ("Augusta National Golf Club", "Augusta, GA", date(2000, 4, 6), date(2000, 4, 9)),
    (2000, "usopen"): ("Pebble Beach Golf Links", "Pebble Beach, CA", date(2000, 6, 15), date(2000, 6, 18)),
    (2000, "open"): ("Old Course at St Andrews", "St Andrews, Scotland", date(2000, 7, 20), date(2000, 7, 23)),
    (2000, "pga"): ("Valhalla Golf Club", "Louisville, KY", date(2000, 8, 17), date(2000, 8, 20)),
}
OUT_POS = {"RETD": "WD", "WD": "WD", "DQ": "DQ", "DSQ": "DQ", "DNS": "DNS", "CUT": "CUT", "MC": "CUT"}


def path_for(root: Path, year: int, name: str) -> Path:
    return root / "history" / f"{year} {name}.html"


def player_name(text: str) -> str:
    """'BJØRN, Thomas*' -> 'Thomas Bjørn'; 'MCILROY, Rory' -> 'Rory Mcilroy' (case is ignored when matching)."""
    text = text.replace("*", "").replace("(Am)", "").strip()
    if "," in text:
        last, first = [p.strip() for p in text.split(",", 1)]
        keep = {"II", "III", "IV", "JR", "JR.", "SR", "SR."}
        last = " ".join("-".join(p if p in keep else (p.capitalize() if p.isupper() else p)
                                 for p in w.split("-"))
                        for w in last.split())
        first = re.sub(r"^(Sir|Dr\.?)\s+", "", first)
        return f"{first} {last}".strip()
    return text


def _int(t):
    t = t.strip()
    return int(t) if re.fullmatch(r"\d{2,3}", t) else None


def _to_par(t):
    t = t.strip().upper().replace("−", "-")
    if t in ("PAR", "E"):
        return 0
    m = re.fullmatch(r"([+-]?)(\d+)", t)
    if not m:
        return None
    return -int(m.group(2)) if m.group(1) == "-" else int(m.group(2))


def _fmt(v):
    if v is None:
        return ""
    return "E" if v == 0 else (f"+{v}" if v > 0 else str(v))


def parse(html: str) -> tuple[list[Row], int | None]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("no table in the saved file")
    header = [c.get_text(" ", strip=True) for c in table.find("thead").find_all("th")]
    col = {h: i for i, h in enumerate(header) if h}
    for need in ("Pos", "Player Name", "Score", "R1", "R2", "R3", "R4"):
        if need not in col:
            raise ValueError(f"column {need} missing; save the table with the window wide enough to show R1-R4")

    recs, missed_cut = [], False
    for tr in table.find("tbody").find_all("tr", recursive=False):
        classes = " ".join(tr.get("class") or [])
        if "table__group" in classes:
            missed_cut = "cut" in tr.get_text(" ", strip=True).lower()
            continue
        if "leaderboard__row" not in classes:
            continue
        cells = [c.get_text(" ", strip=True).replace("​", "") for c in tr.find_all("td", recursive=False)]
        if len(cells) < len(header):
            continue
        pos = cells[col["Pos"]].upper()
        if pos in ("FQ", "DNQ"):
            continue  # failed to qualify: never teed off
        rounds = [_int(cells[col[f"R{i}"]]) for i in range(1, 5)]
        out = OUT_POS.get(pos, "")
        if not out and missed_cut:
            out = "CUT"
        recs.append({"name": player_name(cells[col["Player Name"]]), "pos": pos, "out": out,
                     "rounds": rounds, "to_par": _to_par(cells[col["Score"]])})

    # Made the cut but a round is missing at the end: withdrew
    most = max((sum(1 for x in r["rounds"] if x) for r in recs), default=0)
    for r in recs:
        played = sum(1 for x in r["rounds"] if x)
        if not r["out"] and 0 < played < most:
            r["out"] = "WD"

    votes = Counter()
    for r in recs:
        done = [x for x in r["rounds"] if x]
        if done and r["to_par"] is not None and (sum(done) - r["to_par"]) % len(done) == 0:
            votes[(sum(done) - r["to_par"]) // len(done)] += 1
    par = votes.most_common(1)[0][0] if votes else None

    rows = []
    for r in recs:
        to_par = [None if x is None or par is None else _fmt(x - par) for x in r["rounds"]]
        rows.append(Row(name=r["name"], pos=r["pos"], score="" if r["out"] else _fmt(r["to_par"]),
                        thru="F", out=r["out"], rounds=r["rounds"],
                        total=sum(x for x in r["rounds"] if x) or None, round_to_par=to_par))
    return rows, par


def add_missing(rows: list[Row], extra: list[Row]) -> int:
    """Add golfers from another source (Wikipedia) that the saved leaderboard
    left out, e.g. the European Tour lists only 8 of the 1999 Masters cut."""
    have = {r.name.casefold() for r in rows}
    from .names import key
    have_keys = {key(r.name) for r in rows}
    added = 0
    for r in extra:
        if r.out and r.name.casefold() not in have and key(r.name) not in have_keys:
            rows.append(r)
            added += 1
    return added


def load(root: Path, year: int, key: str, name: str):
    """(rows, venue, start, end) from a saved file, or None if there isn't one."""
    p = path_for(root, year, name)
    if not p.exists():
        return None
    rows, par = parse(p.read_text(encoding="utf-8", errors="replace"))
    course, place, start, end = VENUES.get((year, key), (None, None, None, None))
    venue = {k: v for k, v in {"course": course, "place": place, "par": par}.items() if v}
    return rows, venue, start, end
