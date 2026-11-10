# Majors Pool

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
     and fills in the page, including the Day 1–4 files. Compare a couple of
     them against the files you made by hand. (Backfilled day files come from
     final round scores, so they'll match what you sent at the time.)

From then on it runs by itself. Outside major weeks each run takes a few
seconds and changes nothing.

## Test tournaments

A test tournament is scored exactly like a major, with the same rosters, and
gets its own tab marked "Test". It saves day files but never counts toward
Overall. `config.json` sets them by name (any part of ESPN's tournament name):

```json
"test_tournaments": {
  "every_year": ["THE PLAYERS Championship"],
  "2026": ["Good Good Championship"]
}
```

- **every_year** runs each season. THE PLAYERS in mid-March is a month before
  the Masters, has a strong field (so the name check is meaningful), and shows
  whether ESPN's format still reads correctly.
- **A year** runs only that season, for one-off tests like this weekend.

The test needs that year's teams file in place, so upload it before THE PLAYERS.

## Each year

Upload `teams/<year> Teams.csv` (same format as 2026) before the Masters. The
page switches to the new season automatically, and earlier seasons stay
available in the season menu.

## When something looks wrong

**A golfer shows as DNP but is playing.** The page's **Name check** section
lists every golfer whose name didn't match ESPN exactly, and what it did about
it: accent and punctuation differences are matched automatically, looser
matches are marked **Please confirm**, and golfers with no match count as DNP
with the closest name shown. To fix a match by hand, edit `config.json`:

```json
"name_overrides": {
  "Matt Fitzpatrick": "Matthew Fitzpatrick",
  "Some Golfer": null
}
```

The name on the left is as written in the teams file; the right is ESPN's
spelling, or `null` to force DNP.

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
