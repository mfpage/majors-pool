# Page Family Golf Pool

A web page with live standings for the golf pool on the four majors. GitHub
updates it every 15 minutes during each major and saves an end-of-day CSV
(same layout as the old R script's output) once every golfer in the pool has
finished the round. Nothing needs to run on your computer.

## What's in here

| Path | What it is |
| --- | --- |
| `teams/2026 Teams.csv` | The picks. One column per person, 12 golfers each. Add a new file each year. |
| `config.json` | Manual fixes: name overrides and ESPN event ids (usually left empty). |
| `pool/` | The Python code: reading ESPN, name matching, scoring, the CSV. |
| `docs/` | The web page. `docs/data` and `docs/snapshots` are written by the job. |
| `.github/workflows/update.yml` | The schedule: every 15 minutes. |
| `tests/` | Checks against your 2026 Masters Day 1 file. |

## One-time setup (about 15 minutes)

1. **Create the repository.** On github.com, click **New repository**. Name it
   `majors-pool`, choose **Public**, and click **Create repository**.

2. **Upload the files.** On the new repository's page, click
   **uploading an existing file**, then drag in everything from the unzipped
   `majors-pool` folder, and click **Commit changes**.
   - The `.github` folder is hidden on a Mac. In the Finder window, press
     **Cmd + Shift + .** to show it, then include it in the drag.
   - To check it worked, the repository should show a `.github` folder. If not,
     click **Add file → Create new file**, type `.github/workflows/update.yml`
     as the name, paste in the contents of that file, and commit.

3. **Let the job save its results.** Go to **Settings → Actions → General**.
   Under **Workflow permissions**, choose **Read and write permissions** and
   click **Save**.

4. **Turn on the web page.** Go to **Settings → Pages**. Under **Build and
   deployment**, set Source to **Deploy from a branch**, Branch to **main**,
   folder to **/docs**, and click **Save**. After a minute the page is at
   `https://<your-username>.github.io/majors-pool/`.

5. **Test it on this year's majors.** Go to the **Actions** tab (click
   **I understand my workflows, go ahead and enable them** if asked).
   - Click **Update pool → Run workflow**, choose **check-schedule**, and run it.
     Open the finished run to see the four majors and their ESPN ids.
   - Run it again with **backfill**. This scores all four 2026 majors from ESPN
     and fills in the page, including the Day 1–4 files. Backfill always
     rebuilds the season from scratch, so it's safe to run again. Compare a couple of
     them against the files you made by hand. (Backfilled day files come from
     final round scores, so they'll match what you sent at the time.)

From then on it runs by itself. Outside major weeks each run takes a few
seconds and changes nothing.

## Health check (only you need this)

Every run also scores whatever ESPN shows as this week's tournament, with the
pool's rosters, as if it were a major, and checks each step: the four majors
are on ESPN's schedule, the data sources read, every golfer's status is
recognized, round scores add up, the cut is picked up, and day files save.

Results are on an unlisted page that nothing links to:
`https://<your-username>.github.io/majors-pool/health-4e2e646b/`
(The repository is public, so it's unlisted rather than private.)

When a check newly fails, that run is marked failed in the Actions tab, and
GitHub notifies you the way it does for any failed workflow (email or the
GitHub mobile app, under your GitHub notification settings). You hear about
each problem once, not every 15 minutes. If it's a problem with ESPN, run
**probe-event** with the tournament id shown on the health page and share the
output.

## Each year

Upload `teams/<year> Teams.csv` (same format as 2026) before the Masters. The
page switches to the new season automatically, and earlier seasons stay in
the Season menu.

## Adding past seasons

Upload a teams file for each past year (for example `teams/2019 Teams.csv`),
then run the workflow with **backfill** and type `all` (every year with a teams
file) or one year (like `2019`) in the year box. It scores those seasons from
ESPN, with day files, and they appear in the Season menu. Backfilling a year
rebuilds it from scratch, so it's safe to run again. Day files for past
seasons come from final round scores.

## When something looks wrong

**Checking roster names.** The health page lists all 48 rostered golfers.
A name turns green once ESPN has listed that exact spelling in any
tournament's field that year; most turn green weeks before the Masters, since
every tournament is checked, not just majors. A red name hasn't been seen yet.
That's normal until he plays an event, but if ESPN spells him differently the
page shows ESPN's spelling, and you fix the teams file. (Scoring still matches
accents and small spelling differences automatically, so a red name doesn't
change anyone's score.) To force a match by hand, `config.json` also takes
overrides:

```json
"name_overrides": {
  "Matt Fitzpatrick": "Matthew Fitzpatrick",
  "Some Golfer": null
}
```

The left side is as written in the teams file; the right is ESPN's spelling,
or `null` to force DNP.

**The page shows "Couldn't read ESPN".** The job reads ESPN two different
ways (the leaderboard page, then ESPN's data feed) and uses whichever works.
If both fail, the last good standings stay up with that banner. Run the
workflow with **probe-event** and the event id to see what each source
returns; that output is what to share when asking for a fix.

**A major isn't found.** If ESPN renames one, put its id in `config.json`
(keys: `masters`, `pga`, `usopen`, `open`):

```json
"event_ids": { "2027": { "masters": "401811930" } }
```

## Scoring rules

Unchanged from `Golf Functions.R`:

- CUT, WD, DNP, DQ and DNS golfers don't count.
- Each person's best five scores to par are added up.
- Ranking: more counting scores first, then lowest total, then best single
  score, second best, and so on to the fifth.
- Overall: each person's finishing position in each major, added up. Lowest wins.

Day files are saved once every golfer in the pool has finished that round
(Day 2 also waits for ESPN to post the cut). If a round runs into the next
morning, the file waits, and its scores come from that round's strokes, so
holes already played in the next round don't leak in. Status shows **F**
rather than tee times.

## Running it on your own computer (optional)

```
pip install -r requirements.txt
python -m tests.test_pipeline          # the checks
python -m pool.run --schedule 2026     # list the majors ESPN has
python -m pool.run --probe 401811947   # what ESPN returns for one event
python -m pool.run --backfill          # score this year's majors now
```
