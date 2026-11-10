"""Health check: run the whole pipeline on whatever ESPN is showing this week.

Every run scores the current tournament with the pool's rosters, as if it were
a major, and checks that each piece still works. Results go to an unlisted
page (docs/<health_page>/). When a check newly fails, update() leaves a flag
file so the GitHub run is marked failed and GitHub notifies the owner, once.
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from . import espn
from .scoring import parse_score

KNOWN_STATUSES = {
    "STATUS_FINISH", "STATUS_CUT", "STATUS_SCHEDULED", "STATUS_IN_PROGRESS",
    "STATUS_PLAY_COMPLETE", "STATUS_WITHDRAWN", "STATUS_WD", "STATUS_DISQUALIFIED",
    "STATUS_DQ", "STATUS_SUSPENDED", "STATUS_DELAYED", "STATUS_FINAL",
}
WRITE_EVERY = timedelta(minutes=60)  # unless results change, save at most hourly
CATCH_UP_PER_RUN = 8                  # earlier tournaments read per run for the roster check


def _check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def current_event():
    data = espn._get(espn.SCHEDULE_URL).json()
    events = data.get("events") or []
    if not events:
        return None
    ev = events[0]
    return {"id": str(ev.get("id")), "name": ev.get("name") or ev.get("shortName") or "",
            "start": (ev.get("date") or "")[:10] or None, "end": (ev.get("endDate") or "")[:10] or None}


def roster_status(prev: dict, year: int, teams: dict, fields: list) -> tuple[dict, list]:
    """Track which rostered names ESPN has listed, exactly as written, in any
    field this year. Returns (state to save, rows for the page)."""
    from .names import match_roster
    state = prev.get("roster") if prev.get("roster_year") == year else None
    state = dict(state or {})
    rank = {"spelling": 4, "flipped": 3, "typo": 2, "dnp": 1}
    for tournament, when, field in fields:
        if not field:
            continue
        names = set(field)
        for roster_names in teams.values():
            for name in roster_names:
                rec = state.setdefault(name, {})
                if rec.get("found"):
                    continue
                if name in names:
                    state[name] = {"found": tournament, "date": when}
                    continue
                m = match_roster([name], field)[0]
                hint = m.espn_name or (m.suggestion if m.suggestion and "override" not in m.suggestion else None)
                if hint and rank.get(m.kind, 0) >= rec.get("rank", 0):
                    rec.update({"hint": hint, "rank": rank[m.kind], "kind": m.kind, "where": tournament})
    rows = []
    for person, roster_names in teams.items():
        for name in roster_names:
            rec = state.get(name, {})
            rows.append({"person": person, "name": name, "found": rec.get("found"), "date": rec.get("date"),
                         "hint": rec.get("hint"), "kind": rec.get("kind"), "where": rec.get("where")})
    return state, rows


def run(now: datetime, docs: Path, slug: str, teams: dict, config: dict,
        majors_found: dict, major_entries: list, process_major,
        fields: list | None = None, year: int | None = None) -> bool:
    """Run the checks and write docs/<slug>/health.json. Returns True when a
    check has newly failed (the caller then flags the run as failed)."""
    folder = docs / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "health.json"
    prev = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    checks = []

    # 1. The schedule still finds the majors
    missing = [espn.MAJORS[k][0] for k in espn.MAJORS if k not in majors_found]
    checks.append(_check("Majors on ESPN's schedule",
                         "fail" if missing else "pass",
                         f"Couldn't find: {', '.join(missing)}." if missing else "All four found with dates."))

    # 2. Any major that is being followed right now reads without errors
    errs = [m["name"] for m in major_entries if m.get("error")]
    checks.append(_check("Pool page updates", "fail" if errs else "pass",
                         f"Last update failed for: {', '.join(errs)}." if errs
                         else "No errors on the pool page."))

    # 3. This week's tournament
    entry = prev.get("event")
    rows, raw = None, None
    try:
        ev = current_event()
    except Exception as exc:
        ev = None
        checks.append(_check("This week's tournament", "fail", f"Couldn't read ESPN's scoreboard: {exc}"))
    if ev:
        checks.append(_check("This week's tournament", "pass", f"{ev['name']} (ESPN id {ev['id']})."))
        if not entry or entry.get("event_id") != ev["id"]:
            entry = {"key": "health", "name": ev["name"], "event_id": ev["id"], "state": "upcoming"}
            for f in (folder / "snapshots").glob("*.csv") if (folder / "snapshots").exists() else []:
                f.unlink()
        entry.update({"start": ev["start"], "end": ev["end"]})

        # 4. Main source
        try:
            raw = espn._get(espn.FEED_LEADERBOARD_URL, params={"league": "pga", "event": ev["id"]}).json()
            rows = espn.parse_leaderboard_feed(raw)
            entry["venue"] = espn.venue_from(raw) or entry.get("venue")
            ok, why = espn.looks_valid(rows)
            checks.append(_check("Leaderboard feed (main source)", "pass" if ok else "fail",
                                 f"{len(rows)} golfers read." if ok else why))
            if not ok:
                rows = None
        except Exception as exc:
            checks.append(_check("Leaderboard feed (main source)", "fail", f"{type(exc).__name__}: {exc}"))

        # 5. Backups
        backups = []
        for name, fn in (("leaderboard page", espn.fetch_html), ("scoreboard feed", espn.fetch_feed)):
            try:
                ok, why = espn.looks_valid(fn(ev["id"]))
                backups.append((name, ok, why))
            except Exception as exc:
                backups.append((name, False, type(exc).__name__))
        working = [n for n, ok, _ in backups if ok]
        checks.append(_check("Backup sources", "pass" if working else "warn",
                             f"Working: {', '.join(working)}." if working else
                             "Neither backup reads, so the pool depends on the main source alone. "
                             + "; ".join(f"{n}: {w}" for n, _, w in backups)))

    if rows is not None:
        comp = raw["events"][0]["competitions"][0]
        # 6. Statuses
        seen = {}
        for c in comp.get("competitors") or []:
            n = ((c.get("status") or {}).get("type") or {}).get("name") or "(none)"
            seen[n] = seen.get(n, 0) + 1
        new = {k: v for k, v in seen.items() if k not in KNOWN_STATUSES}
        checks.append(_check("Golfer statuses", "warn" if new else "pass",
                             "New status from ESPN, check it's handled: "
                             + ", ".join(f"{k} ({v})" for k, v in new.items()) if new
                             else "All recognized: " + ", ".join(f"{k.replace('STATUS_', '').title()} {v}"
                                                                  for k, v in sorted(seen.items())) + "."))

        # 7. Round scores add up to the total
        eligible = bad = 0
        for r in rows:
            if r.out or r.thru.isdigit():
                continue
            parts = [parse_score(x) for x in r.round_to_par if x]
            sc = parse_score(r.score)
            if not parts or sc is None:
                continue
            eligible += 1
            bad += sum(parts) != sc
        if not eligible:
            checks.append(_check("Scores add up", "pass", "No finished rounds to check yet."))
        else:
            checks.append(_check("Scores add up", "fail" if bad > 0.05 * eligible else "pass",
                                 f"{eligible - bad} of {eligible} golfers' round scores match their total."))

        # 8. Cut
        cut = sum(r.out == "CUT" for r in rows)
        past_r2 = any(r.rounds[2] for r in rows) or any(
            (c.get("status") or {}).get("period", 0) >= 3 for c in comp.get("competitors") or [])
        if cut:
            checks.append(_check("Cut", "pass", f"{cut} golfers marked cut."))
        elif past_r2 and len(rows) >= 70:
            checks.append(_check("Cut", "warn", "Round 3 has started but nobody is marked cut. "
                                                "Fine if this event has no cut; otherwise the cut isn't being read."))
        else:
            checks.append(_check("Cut", "pass", "Not made yet."))

        # 9. Pool scoring and day files
        try:
            process_major(entry, rows, "leaderboard feed", teams, config, now, folder / "snapshots")
            for s in entry.get("snapshots", []):
                s["file"] = "snapshots/" + Path(s["file"]).name
            lead = min(entry["standings"], key=lambda s: s["rank"])
            in_field = sum(1 for s in entry["standings"] for g in s["golfers"] if g["espn"])
            checks.append(_check("Pool scoring", "pass",
                                 f"{lead['person']} would lead at {lead['total']}. "
                                 f"{in_field} of {sum(len(v) for v in teams.values())} pool golfers are in this field."))
            final = ((comp.get("status") or {}).get("type") or {}).get("name") == "STATUS_FINAL"
            days = sorted(s["day"] for s in entry.get("snapshots", []))
            if final and days != [1, 2, 3, 4]:
                checks.append(_check("Day files", "fail", f"Tournament is final but only days {days or 'none'} saved."))
            else:
                checks.append(_check("Day files", "pass",
                                     ("Saved: " + ", ".join(f"Day {d}" for d in days) + ".") if days
                                     else "None due yet."))
        except Exception as exc:
            checks.append(_check("Pool scoring", "fail", f"{type(exc).__name__}: {exc}"))
            print(traceback.format_exc())

    # 10. Every rostered name has turned up on ESPN at least once this year
    year = year or now.year
    seen_fields = list(fields or [])
    if rows is not None and entry:
        seen_fields.append((entry["name"], now.date().isoformat(), [r.name for r in rows]))
    # Catch up on the season so far: while any name is unfound, read a few of
    # this year's earlier tournaments each run, oldest first.
    scanned = list(prev.get("roster_scanned") or []) if prev.get("roster_year") == year else []
    known = prev.get("roster") if prev.get("roster_year") == year else {}
    all_names = {n for names in teams.values() for n in names}
    if any(not (known or {}).get(n, {}).get("found") for n in all_names):
        try:
            past = [e for e in espn.calendar(year)
                    if e[2] and e[2].year == year and e[2] <= now.date() and e[1] not in scanned]
            for label, eid, start, _ in sorted(past, key=lambda e: e[2])[:CATCH_UP_PER_RUN]:
                scanned.append(eid)
                try:
                    names = [r.name for r in espn.fetch_leaderboard_feed(eid)]
                    seen_fields.append((label, start.isoformat(), names))
                except Exception as exc:
                    print(f"[health] couldn't read {label} for the roster check: {exc}")
        except Exception as exc:
            print(f"[health] couldn't read the season's schedule for the roster check: {exc}")
    roster_state, roster_rows = roster_status(prev, year, teams, seen_fields)
    unseen = [r["name"] for r in roster_rows if not r["found"]]
    checks.append(_check("Roster names",
                         "warn" if unseen else "pass",
                         f"{len(roster_rows) - len(unseen)} of {len(roster_rows)} found on ESPN this year. "
                         f"Not yet: {', '.join(unseen)}." if unseen
                         else f"All {len(roster_rows)} found on ESPN this year, spelled as in the teams file."))

    failing = {c["name"] for c in checks if c["status"] == "fail"}
    newly_failing = failing - set(prev.get("failing", []))

    result = {"checks": checks, "failing": sorted(failing), "event": entry,
              "roster_year": year, "roster": roster_state, "roster_rows": roster_rows,
              "roster_scanned": scanned,
              "event_name": entry["name"] if entry else None,
              "people": list(teams), "year": now.year}
    signature = json.dumps([(c["name"], c["status"], c["detail"] if c["name"] == "Roster names" else "")
                            for c in checks])
    last = prev.get("updated")
    due = not last or now - datetime.fromisoformat(last) >= WRITE_EVERY
    if due or signature != prev.get("signature"):
        result.update({"updated": now.isoformat(timespec="minutes"), "signature": signature})
        path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")

    for c in checks:
        print(f"[health] {c['status']:4}  {c['name']}: {c['detail']}")
    return bool(newly_failing)
