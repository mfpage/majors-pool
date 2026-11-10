"""The end-of-day CSV, laid out exactly like gettourn() in Golf Functions.R."""
from __future__ import annotations

import csv
import io

from .scoring import Standing, fmt_score


def _golfer_cells(g):
    if g is None:
        return ["", "", ""]
    score = g.status if g.out else fmt_score(g.score)
    status = "F" if g.out else g.status
    return [g.roster_name, score, status]


def day_csv(standings: list[Standing]) -> str:
    people = [s.person for s in standings]
    header = [""]
    for p in people:
        header += [f"{p}'s Players", f"{p}'s Performance", f"{p}'s Status"]
    width = len(header)
    blank = [""] * width

    rows = [header]
    # 12 golfers in three groups of four
    for group in range(3):
        for i in range(4):
            row = [""]
            for s in standings:
                row += _golfer_cells(s.golfers[group * 4 + i])
            rows.append(row)
        if group < 2:
            rows.append(blank)
    rows += [blank, blank]

    def info(label, values):
        row = [label]
        for v in values:
            row += ["", v, ""]
        return row

    rows.append(info("Total Score", [fmt_score(s.total) for s in standings]))
    rows.append(info("Number of Scores", [str(s.usable) for s in standings]))
    rows.append(info("Ranking", [str(s.rank) for s in standings]))
    rows += [blank, blank]

    for i in range(5):
        row = ["Top 5" if i == 0 else ""]
        for s in standings:
            row += _golfer_cells(s.top5[i] if i < len(s.top5) else None)
        rows.append(row)

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerows(rows)
    return buf.getvalue()
