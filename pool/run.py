"""One update of the pool. GitHub Actions runs this every 15 minutes.

    python -m pool.run                 # normal run
    python -m pool.run --probe 401811947   # show what each ESPN source returns

It reads teams/<year> Teams.csv, finds the four majors on ESPN, and for any
major happening now (from the Monday before to the Tuesday after) it updates
docs/data/<year>.json, which the web page reads. When a round is finished for
every golfer in the pool, it saves that day's CSV to docs/snapshots/<year>/.
Outside major weeks it changes nothing and finishes in a few seconds.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import espn, health
from .names import match_roster
from .output import day_csv
from .scoring import (Standing, build_golfers, estimate_par, fmt_score, overall,
                      round_complete, standings)

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
TZ = ZoneInfo("America/New_York")
ORDER = ["masters", "pga", "usopen", "open"]
WINDOW_BEFORE = timedelta(days=3)   # field and name check from Monday
WINDOW_AFTER = timedelta(days=2)    # covers Monday finishes


# --------------------------------------------------------------------------
def load_config() -> dict:
    p = ROOT / "config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_teams(year: int) -> dict[str, list[str]] | None:
    p = ROOT / "teams" / f"{year} Teams.csv"
    if not p.exists():
        return None
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if any(c.strip() for c in r)]
    people = [h.strip() for h in rows[0]]
    return {p: [r[i].strip() for r in rows[1:] if i < len(r) and r[i].strip()]
            for i, p in enumerate(people)}


def pick_year(now: datetime) -> int | None:
    years = sorted(int(p.name[:4]) for p in (ROOT / "teams").glob("* Teams.csv") if p.name[:4].isdigit())
    usable = [y for y in years if y <= now.year]
    return usable[-1] if usable else None


# --------------------------------------------------------------------------
def standing_json(s: Standing) -> dict:
    def g(x):
        return {"name": x.roster_name, "espn": x.espn_name,
                "score": None if x.out else fmt_score(x.score),
                "status": x.status, "out": x.out, "counts": x.counts}
    return {"person": s.person, "rank": s.rank, "total": fmt_score(s.total),
            "usable": s.usable, "counting": len(s.top5), "golfers": [g(x) for x in s.golfers]}


def process_major(entry: dict, rows: list, source: str, teams: dict, config: dict,
                  now: datetime, snap_dir: Path) -> None:
    """Score one major from a fresh leaderboard and update its entry in place."""
    field = [r.name for r in rows]
    rows_by_name = {r.name: r for r in rows}
    overrides = config.get("name_overrides", {})

    matches = {p: match_roster(roster, field, overrides) for p, roster in teams.items()}
    par = (entry.get("venue") or {}).get("par") or estimate_par(rows)

    live = standings(teams, {p: build_golfers(teams[p], matches[p], rows_by_name)
                             for p in teams})

    # End-of-day snapshots, in order, for every round now complete
    pool_rows = [rows_by_name.get(m.espn_name) if m.espn_name else None
                 for ms in matches.values() for m in ms]
    saved = {s["day"] for s in entry.get("snapshots", [])}
    for day in range(1, 5):
        if day in saved:
            continue
        if not round_complete(day, pool_rows, rows, par):
            break
        day_st = standings(teams, {p: build_golfers(teams[p], matches[p], rows_by_name, day=day, par=par)
                                   for p in teams})
        season = (entry.get("start") or "")[:4] or str(now.year)
        fname = f"{season} {entry['name']} - Day {day}.csv"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / fname).write_text(day_csv(day_st), encoding="utf-8")
        entry.setdefault("snapshots", []).append({
            "day": day, "file": f"snapshots/{snap_dir.name}/{fname}",
            "saved": now.isoformat(timespec="minutes"),
            "ranks": {s.person: s.rank for s in day_st},
            "totals": {s.person: fmt_score(s.total) for s in day_st},
        })
        saved.add(day)
        print(f"[{entry['name']}] saved Day {day}")

    rounds_done = max(saved) if saved else 0
    playing = any(r.thru.isdigit() for r in rows if not r.out)
    started = playing or any(any(r.rounds) for r in rows)
    if 4 in saved:
        state = "final"
    elif started:
        state = "live"
    else:
        state = "pre"

    entry.update({
        "state": state,
        "round": min(rounds_done + 1, 4) if state != "final" else 4,
        "source": source,
        "standings": [standing_json(s) for s in live],
        "name_check": [
            {"person": p, "roster": m.roster_name, "espn": m.espn_name,
             "kind": m.kind, "suggestion": m.suggestion}
            for p, ms in matches.items() for m in ms if m.kind != "exact"
        ],
        "field_size": len(rows),
        "error": None,
        "updated": now.isoformat(timespec="minutes"),
    })


def in_window(entry: dict, today, force: bool = False) -> bool:
    if entry.get("state") == "final":
        return False
    if force:
        return True
    start, end = entry.get("start"), entry.get("end")
    if not start or not end:
        return True  # dates unknown (manual event id): keep checking until final
    from datetime import date
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    return s - WINDOW_BEFORE <= today <= e + WINDOW_AFTER


def update(now: datetime | None = None, fetch=espn.fetch_leaderboard, find=espn.find_majors,
           backfill: bool = False, check_health: bool = True, year: int | None = None,
           venue=espn.fetch_venue) -> bool:
    """Run one update. Returns True if anything on the site changed.

    backfill=True reads every major for the year regardless of dates, to
    fill in a season already played (day files then come from final scores)."""
    now = now or datetime.now(TZ)
    config = load_config()
    year = year or pick_year(now)
    if year is None:
        print("No teams file found in teams/. Nothing to do.")
        return False
    teams = load_teams(year)

    data_path = DOCS / "data" / f"{year}.json"
    data = json.loads(data_path.read_text(encoding="utf-8")) if data_path.exists() else {}
    if backfill:
        # Rebuild the season from scratch: forget saved results and day files
        data = {}
        snap_dir = DOCS / "snapshots" / str(year)
        if snap_dir.exists():
            for f in snap_dir.glob("*.csv"):
                f.unlink()
    before = json.dumps(data, sort_keys=True)
    data.setdefault("year", year)
    data["people"] = list(teams)
    majors = {m["key"]: m for m in data.get("majors", [])}

    events = find(year, config.get("event_ids", {}).get(str(year), {}))
    for key in list(majors):
        if key not in ORDER:
            del majors[key]  # leftovers from the old test-tournament setup
    for key in ORDER:
        entry = majors.setdefault(key, {"key": key, "name": espn.MAJORS[key][0], "state": "upcoming"})
        ev = events.get(key)
        if ev:
            entry["event_id"] = ev.event_id
            entry["start"] = ev.start.isoformat() if ev.start else entry.get("start")
            entry["end"] = ev.end.isoformat() if ev.end else entry.get("end")

    # Calendar order (Masters, PGA, US Open, Open) when dates are known
    ordered = sorted(majors.values(), key=lambda m: (m.get("start") or "9999", ORDER.index(m["key"])))

    fields = []  # every field read this run, for the roster name check
    for entry in ordered:
        if entry.get("event_id") and not entry.get("venue"):
            try:  # course, location and par, once per major
                entry["venue"] = venue(entry["event_id"]) or None
            except Exception as exc:
                print(f"[{entry['name']}] couldn't read the course details yet: {exc}")
        if not entry.get("event_id") or not in_window(entry, now.date(), backfill):
            continue
        try:
            rows, source = fetch(entry["event_id"])
        except Exception as exc:
            print(f"[{entry['name']}] fetch failed: {exc}")
            entry["error"] = f"Couldn't read ESPN at {now:%-I:%M %p} ET. Showing the last good update."
            entry["error_detail"] = str(exc)[:500]
            continue
        process_major(entry, rows, source, teams, config, now, DOCS / "snapshots" / str(year))
        fields.append((entry["name"], now.date().isoformat(), [r.name for r in rows]))
        entry.pop("error_detail", None)

    # Overall pool: final positions, plus the live position for a major under way
    ranks = {}
    for entry in ordered:
        if entry.get("state") in ("live", "final") and entry.get("standings"):
            ranks[entry["key"]] = {s["person"]: s["rank"] for s in entry["standings"]}
    data["overall"] = overall(list(teams), ranks)
    data["majors"] = ordered

    changed = json.dumps(data, sort_keys=True) != before
    if changed:
        data["updated"] = now.isoformat(timespec="minutes")
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        years = sorted({int(p.stem) for p in (DOCS / "data").glob("[0-9][0-9][0-9][0-9].json")}, reverse=True)
        (DOCS / "data" / "index.json").write_text(json.dumps({"years": years}), encoding="utf-8")
        print(f"Updated {data_path.relative_to(ROOT)}")
    else:
        print("No changes.")

    # Health check on this week's tournament (unlisted page)
    if check_health and not backfill and config.get("health_page"):
        try:
            alert = health.run(now, DOCS, config["health_page"], teams, config, events, ordered, process_major,
                               fields=fields, year=year)
        except Exception as exc:
            print(f"[health] the health check itself crashed: {exc!r}")
            alert = True
        if alert:
            (ROOT / ".alert").write_text("A health check newly failed. See the health page.\n")

    # Weekly heartbeat so GitHub doesn't pause the schedule in the off-season
    week = now.strftime("%G-W%V")
    hb = DOCS / "data" / "heartbeat.txt"
    if not hb.exists() or hb.read_text().strip() != week:
        hb.write_text(week + "\n")
    return changed


# --------------------------------------------------------------------------
def probe(event_id: str) -> None:
    """Print what each ESPN source returns for an event, and save ESPN's raw
    responses to docs/debug/<event_id>/ so they can be inspected."""
    out = DOCS / "debug" / str(event_id)
    out.mkdir(parents=True, exist_ok=True)
    raw = [
        ("page.html", espn.HTML_URL.format(event_id=event_id), None),
        ("feed.json", espn.FEED_EVENT_URL.format(event_id=event_id), None),
        ("feed-alt.json", espn.FEED_LEADERBOARD_URL, {"league": "pga", "event": event_id}),
    ]
    for fname, url, params in raw:
        try:
            r = espn.requests.get(url, params=params, headers=espn.HEADERS, timeout=espn.TIMEOUT)
            (out / fname).write_text(r.text, encoding="utf-8")
            print(f"saved {fname}: HTTP {r.status_code}, {len(r.text):,} characters")
        except Exception as exc:
            (out / (fname + ".error.txt")).write_text(repr(exc), encoding="utf-8")
            print(f"{fname}: FAILED {exc!r}")

    for name, fn in (("leaderboard feed", espn.fetch_leaderboard_feed), ("leaderboard page", espn.fetch_html),
                     ("scoreboard feed", espn.fetch_feed)):
        print(f"\n=== {name} ===")
        try:
            rows = fn(event_id)
            ok, why = espn.looks_valid(rows)
            print(f"{len(rows)} golfers; valid={ok} {why}")
            for r in rows[:12]:
                print(f"  {r.pos:>4}  {r.name:<26} score={r.score or '-':>4}  thru={r.thru or '-':<9}"
                      f" out={r.out or '-':<4} rounds={r.rounds} tot={r.total}")
            outs = [r for r in rows if r.out]
            print(f"  ... {len(outs)} marked out: " + ", ".join(f"{r.name} ({r.out})" for r in outs[:8]))
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")


def name_report(year: int) -> None:
    """After a backfill: any rostered name that wasn't matched exactly in a
    single major that season, with ESPN's near match if there was one."""
    p = DOCS / "data" / f"{year}.json"
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    played = [m for m in data.get("majors", []) if m.get("standings")]
    if not played:
        print(f"[{year}] names: no majors could be read, so no name check.")
        return
    exact = {g["name"] for m in played for s in m["standings"] for g in s["golfers"] if g["espn"] == g["name"]}
    notes = {}
    for m in played:
        for c in m.get("name_check", []):
            if c["roster"] in exact:
                continue
            if c["espn"]:
                notes.setdefault(c["roster"], f"matched to ESPN's {c['espn']} ({c['kind']}) in {m['name']}")
            else:
                notes.setdefault(c["roster"], "not in any major field" + (f"; ESPN has {c['suggestion']}" if c["suggestion"] else ""))
    if not notes:
        print(f"[{year}] names: all matched ESPN exactly.")
        return
    print(f"[{year}] names to look at ({len(notes)}):")
    for name, note in notes.items():
        print(f"    {name}: {note}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", metavar="EVENT_ID")
    ap.add_argument("--schedule", metavar="YEAR", type=int)
    ap.add_argument("--backfill", nargs="?", const="current", metavar="YEAR",
                    help="rebuild a season from ESPN: a year, 'all', or nothing for the current one")
    args = ap.parse_args(argv)
    if args.probe:
        probe(args.probe)
        return
    if args.schedule:
        for k, ev in espn.find_majors(args.schedule).items():
            print(f"{k:8} {ev.name:18} id={ev.event_id} {ev.start} to {ev.end}")
        today = datetime.now(TZ).date()
        print("\nEverything ESPN lists from last week through the next two months:")
        for label, eid, start, end in sorted(espn.calendar(args.schedule), key=lambda e: e[2] or today):
            if start and today - timedelta(days=7) <= start <= today + timedelta(days=60):
                print(f"  {start}  id={eid}  {label}")
        return
    if args.backfill:
        if args.backfill == "all":
            years = sorted(int(p.name[:4]) for p in (ROOT / "teams").glob("* Teams.csv") if p.name[:4].isdigit())
        elif args.backfill == "current":
            years = [None]
        else:
            years = [int(args.backfill)]
        for y in years:
            print(f"\n=== Backfilling {y or 'current season'} ===")
            update(backfill=True, year=y)
            name_report(y or pick_year(datetime.now(TZ)))
        return
    update()


if __name__ == "__main__":
    sys.exit(main())
