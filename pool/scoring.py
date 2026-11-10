"""Pool scoring, ported from Golf Functions.R.

Rules (unchanged from the R script):
- A golfer who is CUT, WD, DNP, DQ or DNS doesn't count.
- Each person's best five remaining scores (to par) are added up.
- Ranking: more usable scores first (up to five), then lower total, then the
  best single score, second best, and so on down to the fifth.
- Overall pool: add up each person's finishing position across the majors.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .espn import OUT_STATUSES, Row
from .names import Match

TEE_TIME = re.compile(r"\d{1,2}:\d{2}\s*[AP]M", re.I)


def parse_score(s: str) -> int | None:
    s = (s or "").strip().upper()
    if s == "E":
        return 0
    m = re.fullmatch(r"([+-]?)(\d+)", s)
    if not m:
        return None
    return -int(m.group(2)) if m.group(1) == "-" else int(m.group(2))


def fmt_score(v: int | None) -> str:
    if v is None:
        return ""
    return "E" if v == 0 else (f"+{v}" if v > 0 else str(v))


@dataclass
class Golfer:
    roster_name: str
    espn_name: str | None
    score: int | None       # to par; None only for golfers who don't count
    status: str             # F, hole number, tee time, CUT, WD, DNP...
    out: bool
    counts: bool = False    # one of the person's best five


@dataclass
class Standing:
    person: str
    golfers: list[Golfer]
    top5: list[Golfer]
    total: int
    usable: int             # golfers still in (the R script's "Number of Scores")
    rank: int = 0

    @property
    def sort_key(self):
        scores = [g.score for g in self.top5]
        tail = [(0, s) for s in scores] + [(1, 0)] * (5 - len(scores))
        return (-len(self.top5), self.total, *tail)


def estimate_par(rows: list[Row]) -> int | None:
    """Course par, from golfers whose finished rounds and score agree."""
    votes = Counter()
    for r in rows:
        done = [x for x in r.rounds if x]
        sc = parse_score(r.score)
        if r.out or sc is None or not done:
            continue
        if r.thru not in ("F", "") and not TEE_TIME.fullmatch(r.thru):
            continue  # mid-round: score includes holes not in the rounds
        diff = sum(done) - sc
        if diff % len(done) == 0:
            votes[diff // len(done)] += 1
    if not votes:
        return None
    par, n = votes.most_common(1)[0]
    return par if 60 <= par <= 75 and n >= 3 else None


def build_golfers(roster: list[str], matches: list[Match], rows_by_name: dict[str, Row],
                  day: int | None = None, par: int | None = None) -> list[Golfer]:
    """Golfer lines for one person.

    day=None is the live view. day=N is the end-of-day view for round N: the
    score is computed from rounds 1..N when possible (so a late finish or an
    early start to the next round doesn't leak in), and the status is "F".
    """
    out = []
    for name, m in zip(roster, matches):
        row = rows_by_name.get(m.espn_name) if m.espn_name else None
        if row is None:
            out.append(Golfer(name, None, None, "DNP", True))
            continue
        if row.out == "CUT" and day == 1 and par and row.rounds[0]:
            # Only matters when filling in a past major: the cut came after Day 1
            out.append(Golfer(name, row.name, row.rounds[0] - par, "F", False))
            continue
        if row.out:
            out.append(Golfer(name, row.name, None, row.out, True))
            continue
        score = parse_score(row.score)
        if day is not None and par:
            done = row.rounds[:day]
            if all(done):
                score = sum(done) - par * day
        if score is None:
            score = 0  # not started yet: level par, as ESPN shows it
        status = "F" if day is not None else row.thru
        out.append(Golfer(name, row.name, score, status, False))
    return out


def standings(teams: dict[str, list[str]], golfers_by_person: dict[str, list[Golfer]]) -> list[Standing]:
    result = []
    for person in teams:
        golfers = golfers_by_person[person]
        alive = [g for g in golfers if not g.out]
        top5 = sorted(alive, key=lambda g: g.score)[:5]  # stable, like R's order()
        for g in top5:
            g.counts = True
        result.append(Standing(person, golfers, top5, sum(g.score for g in top5), len(alive)))
    for i, st in enumerate(sorted(result, key=lambda s: s.sort_key), start=1):
        st.rank = i
    return result


# --------------------------------------------------------------------------
# Is a round finished?
# --------------------------------------------------------------------------
def golfer_done_with(row: Row | None, day: int, par: int | None = None) -> bool:
    if row is None or row.out:
        return True
    if not row.rounds[day - 1]:
        return False
    t = row.thru
    if t in ("F", "") or TEE_TIME.fullmatch(t):
        return True
    # On the course now. A later round with strokes proves `day` is behind him.
    if any(row.rounds[day:]):
        return True
    # Otherwise: if his round columns already explain his whole score, the
    # last one is the round he's playing; if not, he's on the next round.
    done = [x for x in row.rounds if x]
    sc = parse_score(row.score)
    if par is None or sc is None:
        return False
    playing_round = len(done) if sum(done) - par * len(done) == sc else len(done) + 1
    return day < playing_round


def cut_posted(rows: list[Row], par: int | None = None) -> bool:
    if any(r.out == "CUT" for r in rows):
        return True
    # Once round 3 is under way the cut has happened, or there wasn't one
    # (some smaller events have no cut).
    for r in rows:
        if r.out:
            continue
        if r.rounds[2]:
            return True
        if r.thru.isdigit() and r.rounds[1] and golfer_done_with(r, 2, par):
            return True
    return False


def round_complete(day: int, pool_rows: list[Row | None], all_rows: list[Row],
                   par: int | None = None) -> bool:
    """Every golfer in the pool is finished with round `day` (and, for round 2,
    ESPN has posted the cut)."""
    if not all(golfer_done_with(r, day, par) for r in pool_rows):
        return False
    if not any(r.rounds[day - 1] for r in all_rows if not r.out):
        return False  # nobody has a score for that round yet
    if day == 2 and not cut_posted(all_rows, par):
        return False
    return True


# --------------------------------------------------------------------------
# Overall pool
# --------------------------------------------------------------------------
def overall(people: list[str], ranks_by_major: dict[str, dict[str, int]]) -> list[dict]:
    rows = []
    for p in people:
        per = {k: v.get(p) for k, v in ranks_by_major.items()}
        rows.append({"person": p, "ranks": per,
                     "total": sum(r for r in per.values() if r is not None)})
    rows.sort(key=lambda r: r["total"])
    prev, place = None, 0
    for i, r in enumerate(rows, start=1):
        if r["total"] != prev:
            place = i
            prev = r["total"]
        r["place"] = place
    return rows
