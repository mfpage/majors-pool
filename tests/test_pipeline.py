"""End-to-end checks against Michael's real 2026 Masters Day 1 output.

Run:  python -m tests.test_pipeline
"""
import csv
import json
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

from pool import espn, run
from pool.espn import Event, Row
from pool.names import match_roster

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EXPECTED = HERE / "fixtures" / "Masters - Day 1.csv"
PAR = 72

# Names as ESPN might print them, to exercise the name check
ESPN_SPELLING = {"Ludvig Åberg": "Ludvig Aberg", "Rasmus Højgaard": "Rasmus Hojgaard"}


def day1_rows():
    """The field after round 1, built from the expected CSV (tee times = next round)."""
    with EXPECTED.open(encoding="utf-8") as f:
        lines = list(csv.reader(f))
    rows = []
    for line in lines[1:16]:
        for i in range(1, len(line), 3):
            name, score, tee = line[i:i + 3]
            if not name:
                continue
            s = 0 if score == "E" else int(score)
            rows.append(Row(name=ESPN_SPELLING.get(name, name), score=score, thru=tee,
                            rounds=[PAR + s, None, None, None], total=PAR + s))
    for k in range(40):  # rest of the field
        rows.append(Row(name=f"Field Player{k}", score="+1", thru="9:00 AM",
                        rounds=[73, None, None, None], total=73))
    return rows


def fake_find(year, overrides, tests=None):
    return {"masters": Event("masters", "Masters", "1", date(2026, 4, 9), date(2026, 4, 12))}


def in_sandbox(fn):
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(REPO / "teams", tmp / "teams")
    shutil.copy(REPO / "config.json", tmp / "config.json")
    old = run.ROOT, run.DOCS
    run.ROOT, run.DOCS = tmp, tmp / "docs"
    try:
        return fn(tmp)
    finally:
        run.ROOT, run.DOCS = old
        shutil.rmtree(tmp)


def test_day1_matches_original():
    def go(tmp):
        rows = day1_rows()
        run.update(datetime(2026, 4, 9, 20, 0, tzinfo=run.TZ),
                   fetch=lambda eid: (rows, "html"), find=fake_find)
        got = (tmp / "docs/snapshots/2026/Masters - Day 1.csv").read_text(encoding="utf-8")
        with EXPECTED.open(encoding="utf-8") as f:
            exp = list(csv.reader(f))
        for line in exp[1:]:        # the only intended change: status shows F
            for i in range(3, len(line), 3):
                if line[i]:
                    line[i] = "F"
        got_rows = list(csv.reader(got.splitlines()))
        assert got_rows == exp, "\n".join(
            f"row {n}: got {g}\n        exp {e}" for n, (g, e) in enumerate(zip(got_rows, exp)) if g != e)
        data = json.loads((tmp / "docs/data/2026.json").read_text())
        m = data["majors"][0]
        assert m["state"] == "live" and m["round"] == 2, (m["state"], m["round"])
        kinds = {c["roster"]: c["kind"] for c in m["name_check"]}
        assert kinds == {"Ludvig Åberg": "spelling", "Rasmus Højgaard": "spelling"}, kinds
        return data
    data = in_sandbox(go)
    print("ok  Day 1 CSV matches the original (statuses now F); ranks",
          {o["person"]: o["place"] for o in data["overall"]})


def test_round2_waits_for_cut():
    def go(tmp):
        rows = day1_rows()
        stamp = datetime(2026, 4, 9, 20, 0, tzinfo=run.TZ)
        run.update(stamp, fetch=lambda e: (rows, "html"), find=fake_find)
        for r in rows:              # everyone finishes round 2, cut not posted yet
            r.rounds[1] = 72
            r.thru = "8:00 AM"
        run.update(stamp.replace(day=10), fetch=lambda e: (rows, "html"), find=fake_find)
        assert not (tmp / "docs/snapshots/2026/Masters - Day 2.csv").exists(), "saved before cut"
        rows[-1].out, rows[-1].score, rows[-1].thru = "CUT", "", "F"  # ESPN posts the cut
        rows[0].out, rows[0].score, rows[0].thru = "CUT", "", "F"     # incl. one pool golfer
        run.update(stamp.replace(day=10, hour=21), fetch=lambda e: (rows, "html"), find=fake_find)
        out = (tmp / "docs/snapshots/2026/Masters - Day 2.csv").read_text(encoding="utf-8")
        assert '"Scottie Scheffler","CUT","F"' in out, out[:300]
    in_sandbox(go)
    print("ok  Day 2 waits for ESPN to post the cut, then marks cut golfers")


def test_late_finish_and_early_start():
    def go(tmp):
        rows = day1_rows()
        rows[0].rounds[0], rows[0].score, rows[0].thru = None, "-1", "14"  # weather: still on 14
        run.update(datetime(2026, 4, 9, 20, 0, tzinfo=run.TZ), fetch=lambda e: (rows, "html"), find=fake_find)
        assert not (tmp / "docs/snapshots/2026/Masters - Day 1.csv").exists(), "saved mid-round"
        # Next morning: he finishes R1 (-2) and another golfer is already 5 holes into R2 at -4
        rows[0].rounds[0], rows[0].score, rows[0].thru = 70, "-2", "F"
        burns = next(r for r in rows if r.name == "Sam Burns")
        burns.score, burns.thru = "-7", "5"
        run.update(datetime(2026, 4, 10, 9, 0, tzinfo=run.TZ), fetch=lambda e: (rows, "html"), find=fake_find)
        out = (tmp / "docs/snapshots/2026/Masters - Day 1.csv").read_text(encoding="utf-8")
        assert '"Sam Burns","-5","F"' in out, "Day 1 must use round 1 strokes, not the live score"
    in_sandbox(go)
    print("ok  delayed round waits; Day 1 file ignores holes already played in round 2")


def test_name_matching():
    field = ["Ludvig Aberg", "J.J. Spaun", "Si Woo Kim", "Rasmus Højgaard", "Nicolai Hojgaard",
             "Sami Valimaki", "Matthew Fitzpatrick", "Cameron Smith", "Cam Davis"]
    roster = ["Ludvig Åberg", "JJ Spaun", "Si-Woo Kim", "Rasmus Hojgaard", "Sami Välimäki",
              "Matt Fitzpatrick", "Cameron Smith", "Tiger Woods"]
    got = {m.roster_name: (m.espn_name, m.kind) for m in match_roster(roster, field)}
    assert got["Ludvig Åberg"] == ("Ludvig Aberg", "spelling")
    assert got["JJ Spaun"] == ("J.J. Spaun", "spelling")
    assert got["Si-Woo Kim"] == ("Si Woo Kim", "spelling")
    assert got["Rasmus Hojgaard"] == ("Rasmus Højgaard", "spelling")
    assert got["Sami Välimäki"] == ("Sami Valimaki", "spelling")
    assert got["Matt Fitzpatrick"] == ("Matthew Fitzpatrick", "likely"), got["Matt Fitzpatrick"]
    assert got["Cameron Smith"] == ("Cameron Smith", "exact")
    assert got["Tiger Woods"][1] == "dnp"
    over = match_roster(["Matt Fitzpatrick"], field, {"Matt Fitzpatrick": None})[0]
    assert over.kind == "override" and over.espn_name is None
    print("ok  name matching: accents, initials, hyphens, nicknames, true DNP, overrides")


def test_html_parser():
    html = (HERE / "fixtures" / "leaderboard.html").read_text(encoding="utf-8")
    rows = espn.parse_html(html)
    by = {r.name: r for r in rows}
    assert by["Rory McIlroy"].score == "-5" and by["Rory McIlroy"].rounds[0] == 67
    assert by["Jon Rahm"].out == "CUT" and by["Jon Rahm"].thru == "F"
    assert by["Shane Lowry"].thru == "12"
    print("ok  ESPN table parser")


def test_test_tournament_excluded():
    def find(year, overrides, tests=None):
        return {"masters": Event("masters", "Masters", "1", date(2026, 4, 9), date(2026, 4, 12)),
                "test-good-good": Event("test-good-good", "Good Good Championship", "2",
                                        date(2026, 4, 9), date(2026, 4, 12))}
    def go(tmp):
        rows = day1_rows()
        run.update(datetime(2026, 4, 9, 20, 0, tzinfo=run.TZ), fetch=lambda e: (rows, "html"), find=find)
        data = json.loads((tmp / "docs/data/2026.json").read_text())
        keys = {m["key"]: m for m in data["majors"]}
        assert keys["test-good-good"]["test"] and keys["test-good-good"]["standings"]
        assert all(set(o["ranks"]) == {"masters"} for o in data["overall"])
        assert (tmp / "docs/snapshots/2026/Good Good Championship - Day 1.csv").exists()
    in_sandbox(go)
    print("ok  test tournaments score and save files but stay out of Overall")


if __name__ == "__main__":
    test_name_matching()
    test_html_parser()
    test_day1_matches_original()
    test_round2_waits_for_cut()
    test_late_finish_and_early_start()
    test_test_tournament_excluded()
    print("all passed")
