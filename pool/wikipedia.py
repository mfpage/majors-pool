"""One-time source for majors ESPN doesn't have (1999 and 2000).

Each major's Wikipedia page lists every golfer who made the cut with their
four round scores (70-66-73-72=281), and the golfers who missed the cut or
withdrew. That's turned into the same Row objects the ESPN code produces, so
scoring, day files and the page work unchanged. Only used by backfill.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .espn import Row

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "PageFamilyGolfPool/1.0 (one-time backfill of 1999-2000 majors)"}
TITLES = {
    "masters": "{y} Masters Tournament",
    "pga": "{y} PGA Championship",
    "usopen": "{y} U.S. Open (golf)",
    "open": "{y} Open Championship",
}
PLACE = re.compile(r"^(T?\d+|CUT|MC|WD|DQ|DNS)$", re.I)
SCORE = re.compile(r"^(\d{2,3}(?:\s*[-–]\s*\d{2,3})*)(?:\s*=\s*(\d{2,3}))?$")
TOPAR = re.compile(r"^([+−-]?)(\d+)$|^E$")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}
DEBUG_DIR: Path | None = None  # set by run.py to save the raw pages for checking


def fetch_html(title: str) -> str:
    r = requests.get(API, params={"action": "parse", "page": title, "prop": "text", "format": "json",
                                  "formatversion": 2, "redirects": 1}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise LookupError(f"Wikipedia: {data['error'].get('info')}")
    return data["parse"]["text"]


def fetch_major(year: int, key: str):
    """Return (rows, venue, start, end, title) for one major from Wikipedia."""
    title = TITLES[key].format(y=year)
    html = fetch_html(title)
    if DEBUG_DIR:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / f"{title}.html").write_text(html, encoding="utf-8")
    rows, par = parse_leaderboard(html)
    venue, start, end = parse_infobox(html, year)
    if par and not venue.get("par"):
        venue["par"] = par
    return rows, venue, start, end, title


def _num_to_par(text: str) -> int | None:
    t = text.strip().replace("−", "-").replace("–", "-")
    if t.upper() == "E":
        return 0
    m = re.fullmatch(r"([+-]?)(\d+)", t)
    if not m:
        return None
    return -int(m.group(2)) if m.group(1) == "-" else int(m.group(2))


def _clean_name(text: str) -> str:
    text = re.sub(r"\(a\)|\[[^\]]*\]", "", text)  # amateur marker, footnotes
    return re.sub(r"\s+", " ", text).strip()


def parse_leaderboard(html: str) -> tuple[list[Row], int | None]:
    soup = BeautifulSoup(html, "html.parser")
    best: dict[str, dict] = {}
    for table in soup.find_all("table", class_=re.compile("wikitable")):
        header = [c.get_text(" ", strip=True).lower() for c in table.find("tr").find_all(["th", "td"])]
        if not any("player" in h for h in header) or not any("score" in h for h in header):
            continue
        place = ""
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            texts = [c.get_text(" ", strip=True) for c in cells]
            if not texts:
                continue
            if PLACE.match(texts[0]):
                place = texts[0].upper()
            # the player is the cell with a link to a person (after the flag)
            name = None
            for c in cells:
                links = [a for a in c.find_all("a") if not a.find_parent(class_="flagicon")]
                if c.find(class_="flagicon") and links:
                    name = _clean_name(links[-1].get_text(" ", strip=True))
                    break
            score_text = next((t for t in texts if SCORE.match(t.replace(" ", ""))), None)
            if not name or not score_text:
                continue
            parts = [int(x) for x in re.split(r"[-–]", score_text.split("=")[0].replace(" ", "")) if x]
            if not all(55 <= p <= 95 for p in parts):
                continue
            idx = texts.index(score_text)
            to_par = next((_num_to_par(t) for t in texts[idx + 1:idx + 2] if _num_to_par(t) is not None), None)
            prev = best.get(name)
            if prev is None or len(parts) > len(prev["rounds"]) or \
                    (len(parts) == len(prev["rounds"]) and place in ("CUT", "MC", "WD", "DQ", "DNS")):
                best[name] = {"rounds": parts, "place": place, "to_par": to_par}

    # Course par: total strokes minus score to par, per round
    votes = Counter()
    for rec in best.values():
        if rec["to_par"] is not None:
            diff = sum(rec["rounds"]) - rec["to_par"]
            if diff % len(rec["rounds"]) == 0:
                votes[diff // len(rec["rounds"])] += 1
    par = votes.most_common(1)[0][0] if votes else None

    rows = []
    for name, rec in best.items():
        place = rec["place"]
        out = {"CUT": "CUT", "MC": "CUT", "WD": "WD", "DQ": "DQ", "DNS": "DNS"}.get(place, "")
        rounds = (rec["rounds"] + [None] * 4)[:4]
        to_par = [None] * 4
        if par:
            to_par = [None if r is None else _fmt(r - par) for r in rounds]
        total_par = sum(r - par for r in rec["rounds"]) if par else rec["to_par"]
        rows.append(Row(name=name, pos=place, score="" if out else _fmt(total_par), thru="F", out=out,
                        rounds=rounds, total=sum(rec["rounds"]), round_to_par=to_par))
    return rows, par


def _fmt(v):
    if v is None:
        return ""
    return "E" if v == 0 else (f"+{v}" if v > 0 else str(v))


def parse_infobox(html: str, year: int):
    soup = BeautifulSoup(html, "html.parser")
    box = soup.find("table", class_=re.compile("infobox"))
    info = {}
    if box:
        for tr in box.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th and td:
                info[th.get_text(" ", strip=True).lower()] = re.sub(r"\[[^\]]*\]", "", td.get_text(" ", strip=True))
    venue = {}
    course = info.get("course(s)") or info.get("course")
    if course:
        venue["course"] = course.split(" (")[0].strip()
    if info.get("location"):
        venue["place"] = info["location"].split("(")[0].strip().rstrip(",")
    if info.get("par"):
        m = re.search(r"\d{2}", info["par"])
        if m:
            venue["par"] = int(m.group(0))
    if info.get("length"):
        m = re.search(r"([\d,]{4,6})\s*y", info["length"])
        if m:
            venue["yards"] = int(m.group(1).replace(",", ""))
    start = end = None
    dates = info.get("dates") or info.get("date") or ""
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*[–-]\s*(?:([A-Za-z]+)\s+)?(\d{1,2})", dates)
    if m and m.group(1).lower() in MONTHS:
        m1 = MONTHS[m.group(1).lower()]
        m2 = MONTHS.get((m.group(3) or m.group(1)).lower(), m1)
        try:
            start, end = date(year, m1, int(m.group(2))), date(year, m2, int(m.group(4)))
        except ValueError:
            pass
    return venue, start, end
