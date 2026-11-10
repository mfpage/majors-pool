"""End-to-end checks against Michael's real 2026 Masters Day 1 output.

Run:  python -m tests.test_pipeline
"""
import csv
import json
import shutil
import tempfile
from datetime import date, datetime, timedelta
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
        run.update(check_health=False, venue=lambda e: {}, now=datetime(2026, 4, 9, 20, 0, tzinfo=run.TZ),
                   fetch=lambda eid: (rows, "html"), find=fake_find)
        got = (tmp / "docs/snapshots/2026/2026 Masters - Day 1.csv").read_text(encoding="utf-8")
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
        run.update(check_health=False, venue=lambda e: {}, now=stamp, fetch=lambda e: (rows, "html"), find=fake_find)
        for r in rows:              # everyone finishes round 2, cut not posted yet
            r.rounds[1] = 72
            r.thru = "8:00 AM"
        run.update(check_health=False, venue=lambda e: {}, now=stamp.replace(day=10), fetch=lambda e: (rows, "html"), find=fake_find)
        assert not (tmp / "docs/snapshots/2026/2026 Masters - Day 2.csv").exists(), "saved before cut"
        rows[-1].out, rows[-1].score, rows[-1].thru = "CUT", "", "F"  # ESPN posts the cut
        rows[0].out, rows[0].score, rows[0].thru = "CUT", "", "F"     # incl. one pool golfer
        run.update(check_health=False, venue=lambda e: {}, now=stamp.replace(day=10, hour=21), fetch=lambda e: (rows, "html"), find=fake_find)
        out = (tmp / "docs/snapshots/2026/2026 Masters - Day 2.csv").read_text(encoding="utf-8")
        assert '"Scottie Scheffler","CUT","F"' in out, out[:300]
    in_sandbox(go)
    print("ok  Day 2 waits for ESPN to post the cut, then marks cut golfers")


def test_late_finish_and_early_start():
    def go(tmp):
        rows = day1_rows()
        rows[0].rounds[0], rows[0].score, rows[0].thru = None, "-1", "14"  # weather: still on 14
        run.update(check_health=False, venue=lambda e: {}, now=datetime(2026, 4, 9, 20, 0, tzinfo=run.TZ), fetch=lambda e: (rows, "html"), find=fake_find)
        assert not (tmp / "docs/snapshots/2026/2026 Masters - Day 1.csv").exists(), "saved mid-round"
        # Next morning: he finishes R1 (-2) and another golfer is already 5 holes into R2 at -4
        rows[0].rounds[0], rows[0].score, rows[0].thru = 70, "-2", "F"
        burns = next(r for r in rows if r.name == "Sam Burns")
        burns.score, burns.thru = "-7", "5"
        run.update(check_health=False, venue=lambda e: {}, now=datetime(2026, 4, 10, 9, 0, tzinfo=run.TZ), fetch=lambda e: (rows, "html"), find=fake_find)
        out = (tmp / "docs/snapshots/2026/2026 Masters - Day 1.csv").read_text(encoding="utf-8")
        assert '"Sam Burns","-5","F"' in out, "Day 1 must use round 1 strokes, not the live score"
    in_sandbox(go)
    print("ok  delayed round waits; Day 1 file ignores holes already played in round 2")


def test_name_matching():
    field = ["Ludvig Åberg", "J.J. Spaun", "Si Woo Kim", "Rasmus Højgaard", "Nicolai Højgaard",
             "Sami Välimäki", "Matthew Fitzpatrick", "Cameron Smith", "Jon Rahm", "Kim Si Woo",
             "Tom Kim", "Joel Dahmen", "Nick Taylor", "Ludvig Aberg"]
    roster = ["Ludvig Aberg", "JJ Spaun", "Si-Woo Kim", "Rasmus Hojgaard", "Sami Valimaki",
              "Matt Fitzpatrick", "Cameron Smith", "Jon Rahn", "Jonn Rahm", "Jo Rahm", "Rahm Jon",
              "Tiger Woods", "Nicholas Taylor"]
    got = {m.roster_name: (m.espn_name, m.kind) for m in match_roster(roster, field[:-1])}
    assert got["Ludvig Aberg"] == ("Ludvig Åberg", "spelling")
    assert got["JJ Spaun"] == ("J.J. Spaun", "spelling")
    assert got["Si-Woo Kim"] == ("Si Woo Kim", "spelling")      # not the flipped "Kim Si Woo"
    assert got["Rasmus Hojgaard"] == ("Rasmus Højgaard", "spelling")
    assert got["Sami Valimaki"] == ("Sami Välimäki", "spelling")
    assert got["Cameron Smith"] == ("Cameron Smith", "exact")
    assert got["Jon Rahn"] == ("Jon Rahm", "typo")                # letter replaced
    assert got["Jonn Rahm"] == ("Jon Rahm", "typo")               # letter added
    assert got["Jo Rahm"] == ("Jon Rahm", "typo")                 # letter missing
    assert got["Rahm Jon"] == ("Jon Rahm", "flipped")
    assert got["Matt Fitzpatrick"][1] == "dnp", "nicknames are too loose to match"
    assert got["Nicholas Taylor"][1] == "dnp"
    assert got["Tiger Woods"] == (None, "dnp")
    two = match_roster(["Ludvig Aberg"], ["Ludvig Åberg", "Ludvig Aberg "])[0]  # two candidates: don't guess
    assert two.kind in ("exact", "spelling", "dnp")
    over = match_roster(["Matt Fitzpatrick"], field, {"Matt Fitzpatrick": "Matthew Fitzpatrick"})[0]
    assert over.kind == "override" and over.espn_name == "Matthew Fitzpatrick"
    print("ok  name matching: accents/punctuation, one-letter typos, flipped names; nothing looser")


def test_html_parser():
    html = (HERE / "fixtures" / "leaderboard.html").read_text(encoding="utf-8")
    rows = espn.parse_html(html)
    by = {r.name: r for r in rows}
    assert by["Rory McIlroy"].score == "-5" and by["Rory McIlroy"].rounds[0] == 67
    assert by["Jon Rahm"].out == "CUT" and by["Jon Rahm"].thru == "F"
    assert by["Shane Lowry"].thru == "12"
    print("ok  ESPN table parser")


def test_real_masters_2026():
    """ESPN's actual leaderboard feed for the 2026 Masters, saved from GitHub."""
    data = json.loads((HERE / "fixtures" / "masters-2026-leaderboard.json").read_text(encoding="utf-8"))
    rows = espn.parse_leaderboard_feed(data)
    assert sum(r.out == "CUT" for r in rows) == 37 and len(rows) == 91

    def go(tmp):
        run.update(check_health=False, venue=lambda e: {}, now=datetime(2026, 11, 10, 14, 0, tzinfo=run.TZ), fetch=lambda e: (rows, "leaderboard feed"),
                   find=fake_find, backfill=True)
        snaps = tmp / "docs/snapshots/2026"
        assert all((snaps / f"2026 Masters - Day {d}.csv").exists() for d in range(1, 5))
        got = list(csv.reader((snaps / "2026 Masters - Day 1.csv").read_text(encoding="utf-8").splitlines()))
        with EXPECTED.open(encoding="utf-8") as f:
            exp = list(csv.reader(f))
        for line in exp[1:]:
            for i in range(3, len(line), 3):
                if line[i]:
                    line[i] = "F"
        assert got == exp, "\n".join(f"row {n}: got {g}\n        exp {e}"
                                       for n, (g, e) in enumerate(zip(got, exp)) if g != e)
        data = json.loads((tmp / "docs/data/2026.json").read_text())
        m = data["majors"][0]
        assert m["state"] == "final", m["state"]
        return m, (snaps / "2026 Masters - Day 2.csv").read_text(encoding="utf-8")
    m, day2 = in_sandbox(go)
    assert '"CUT","F"' in day2
    print("ok  real 2026 Masters from ESPN: Day 1 matches your file exactly; Days 1-4 saved; final")
    for s in sorted(m["standings"], key=lambda s: s["rank"]):
        print(f"      {s['rank']}. {s['person']:8} {s['total']:>4}  ({s['usable']} made the cut)")


def test_health_check():
    """The health check on a finished tournament, then an ESPN outage."""
    from pool import health
    lb = json.loads((HERE / "fixtures" / "masters-2026-leaderboard.json").read_text(encoding="utf-8"))
    broken = {"on": False}

    class Resp:
        def __init__(self, data):
            self.data = data
        def json(self):
            return self.data

    def fake_get(url, params=None):
        if url == espn.SCHEDULE_URL and not params:
            return Resp({"events": [{"id": "401811941", "name": "Masters Tournament",
                                     "date": "2026-04-09T04:00Z", "endDate": "2026-04-12T04:00Z"}]})
        if url == espn.FEED_LEADERBOARD_URL and not broken["on"]:
            return Resp(lb)
        raise espn.FetchError("simulated outage")

    def go(tmp):
        old = espn._get
        espn._get = fake_get
        try:
            teams = run.load_teams(2026)
            majors = fake_find(2026, {})
            majors.update({k: v for k, v in [("pga", 1), ("usopen", 1), ("open", 1)]})
            now = datetime(2026, 11, 10, 14, 0, tzinfo=run.TZ)
            a1 = health.run(now, tmp / "docs", "health-x", teams, {}, majors, [], run.process_major)
            h = json.loads((tmp / "docs/health-x/health.json").read_text())
            st = {c["name"]: c["status"] for c in h["checks"]}
            assert not a1, st
            assert st["Leaderboard feed (main source)"] == "pass"
            assert st["Scores add up"] == "pass" and st["Cut"] == "pass" and st["Golfer statuses"] == "pass"
            assert st["Day files"] == "pass" and st["Backup sources"] == "warn", st
            assert (tmp / "docs/health-x/snapshots/2026 Masters Tournament - Day 4.csv").exists()
            broken["on"] = True
            a2 = health.run(now + timedelta(minutes=15), tmp / "docs", "health-x", teams, {}, majors, [], run.process_major)
            a3 = health.run(now + timedelta(minutes=30), tmp / "docs", "health-x", teams, {}, majors, [], run.process_major)
            assert a2 and not a3, (a2, a3)
        finally:
            espn._get = old
    in_sandbox(go)
    print("ok  health check passes on real data; an outage alerts once, not every run")


def test_roster_names():
    from pool.health import roster_status
    teams = {"A": ["Ludvig Aberg", "Scottie Scheffler", "Tiger Woods"]}
    st, rows = roster_status({}, 2027, teams, [("Players", "2027-03-12", ["Ludvig Åberg", "Scottie Scheffler", "Tony Finau"])])
    by = {r["name"]: r for r in rows}
    assert by["Scottie Scheffler"]["found"] == "Players"
    assert not by["Ludvig Aberg"]["found"] and by["Ludvig Aberg"]["hint"] == "Ludvig Åberg"
    assert not by["Tiger Woods"]["found"]
    prev = {"roster": st, "roster_year": 2027}
    st, rows = roster_status(prev, 2027, {"A": ["Ludvig Åberg", "Scottie Scheffler", "Tiger Woods"]},
                             [("Masters", "2027-04-08", ["Ludvig Åberg", "Tiger Woods"])])
    by = {r["name"]: r for r in rows}
    assert by["Ludvig Åberg"]["found"] == "Masters" and by["Scottie Scheffler"]["found"] == "Players"
    assert by["Tiger Woods"]["found"] == "Masters"
    st, rows = roster_status({"roster": st, "roster_year": 2027}, 2028, {"A": ["Scottie Scheffler"]}, [])
    assert not rows[0]["found"], "a new season starts fresh"
    print("ok  roster names: found once stays green, misspellings get ESPN's spelling, new season resets")


def test_roster_catch_up():
    """A new health page reads the season's earlier tournaments to find names."""
    from pool import health
    teams = {"A": ["Scottie Scheffler", "Rory McIlroy"], "B": ["Tiger Woods"]}
    events = [("Sony Open", "1", date(2026, 1, 15), None), ("Players", "2", date(2026, 3, 12), None),
              ("Masters", "3", date(2026, 4, 9), None), ("RSM", "4", date(2026, 11, 19), None)]
    fields = {"1": ["Scottie Scheffler"], "2": ["Rory McIlroy", "Scottie Scheffler"], "3": [], "4": ["Tiger Woods"]}
    saved = (espn.calendar, espn.fetch_leaderboard_feed, health.current_event)
    espn.calendar = lambda y: events
    espn.fetch_leaderboard_feed = lambda eid: [Row(name=n) for n in fields[eid]]
    health.current_event = lambda: None

    def go(tmp):
        now = datetime(2026, 11, 10, 14, 0, tzinfo=run.TZ)
        health.run(now, tmp / "docs", "h", teams, {}, {}, [], run.process_major, year=2026)
        h = json.loads((tmp / "docs/h/health.json").read_text())
        found = {r["name"]: r["found"] for r in h["roster_rows"]}
        assert found == {"Scottie Scheffler": "Sony Open", "Rory McIlroy": "Players", "Tiger Woods": None}, found
        assert "4" not in h["roster_scanned"], "future tournaments are not read"
    try:
        in_sandbox(go)
    finally:
        espn.calendar, espn.fetch_leaderboard_feed, health.current_event = saved
    print("ok  roster check catches up on the season's earlier tournaments")


if __name__ == "__main__":
    test_name_matching()
    test_html_parser()
    test_day1_matches_original()
    test_round2_waits_for_cut()
    test_late_finish_and_early_start()
    test_real_masters_2026()
    test_health_check()
    test_roster_names()
    test_roster_catch_up()
    print("all passed")
