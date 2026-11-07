# Published datasets

- **Purpose:** what `build_stats.py` publishes in `site/data/`, the schedule and participation metrics behind the Ladder data panels, how season ratings are locked into ledgers, and how chest counts are parsed from comments.
- **Read before:** changing a field of `race-stats.json`, `participants.json` or `comments.json`, the season lock, or the chest-count parser. A new field must fit [Privacy](../README.md#privacy), and a schema change bumps the version in Python and in JS together.
- **Code:** `scripts/build_stats.py`, `scripts/participant_stats.py`, `scripts/comment_stats.py`, `scripts/season_ratings.py`. The rating itself is in the [README](../README.md#ladder-rating), and tags, badges and records are in [highlights.md](highlights.md).

## The three files

`build_stats.py` builds three datasets from the archive and writes each one atomically, minified:

| File | Contents |
|---|---|
| `site/data/race-stats.json` | The anonymous aggregate behind the Ladder data panels (see [Schedule and participation metrics](#schedule-and-participation-metrics)). |
| `site/data/participants.json` | The per-racer dataset. Each race carries its Eastern Time date and start hour, its seed preset, its derived tags and its season (see [Season fields](#season-fields)). Each racer carries an overall rating and one for each mode they have raced, badges, streaks and a claim to fame. `avatar_changes` lists the racers whose avatar changed (see [Avatar changes](avatars.md#avatar-changes)). |
| `site/data/comments.json` | The racers' post-race comments, each with the chest count parsed from it (see [Chest counts](#chest-counts-cr-and-cph)). The page fetches it after its first render, so it never delays the board. |

`race-stats.json` and `participants.json` carry a schema version, and the page refuses data whose version it does not expect. `site/avatars/` holds the mirrored avatar images (see [Avatar mirror](avatars.md#avatar-mirror)). What each of these may contain is fixed by [Privacy](../README.md#privacy).

### Season fields

The season fields of `participants.json`:

- **`seasons`:** an array, oldest first, of `{n, first, last, ended}` for every season that holds at least one race. `first` and `last` are the Eastern Time dates of the season's first and latest race. `ended` is true once the season is locked or a later season has started. The current season is the last element. The array is empty when there is no schedule, and the page then shows everything all-time.
- **`races[].s`:** the race's season number. It is absent for a race with no season.
- **Entries:** `races[].e[k]` is `[player, place, status, seconds, overall_after, mode_after, season_after, season_mode_after]`. `[6]` and `[7]` are integers when the race has `s`, and both `null` otherwise.
- **No per-player season fields.** A racer's standing in a season is derived from their entries: their last `[6]` among the season's races for the overall rating, and their last `[7]` among the season's races in a mode for that mode's rating. Their season finishes are their finisher entries in those races, and they are provisional below 5.
- **`diagnostics.seasons`:** `stray_slots`, `races_without_season`, `left_out_of_locked_seasons` and `mismatched_locked_races`.
- `rating`, each player's rating, $\mu$, $\sigma$, finishes and mode ratings, and `leaderboards` are all-time.

## Schedule and participation metrics

`build_stats.py` computes these for `race-stats.json`. They drive the [Ladder data panels](dashboard.md#ladder-data-panels), and the slots they count are defined under [Schedule](definitions.md#schedule).

- **Periods** are calendar quarters (`Q1` to `Q4`). A race belongs to the quarter of its Eastern Time start, and a counted slot to the quarter of its own Eastern Time start. Edge periods are marked partial when the archive does not cover the full quarter. Each period keeps the finished-race summary and the schedule metrics below, so its slot counts add up to the overall ones. A counted slot in a quarter with no finished race is a build error.
- **Race turnout** uses `entrants_count`, which includes repeat participants and entrants who were disqualified or forfeited. It measures race entries, not people.
- **Monthly participation** is the average entries per finished race: total race entries divided by the finished races in that calendar month. A month's finished-race summary also gives the median, the middle half (quartiles) and the 95% range.
- **Entries per scheduled day** is the entries of that month's finished races that started on scheduled days (`scheduled_day_entrant_total`), divided by the month's scheduled days (`scheduled_day_count`, see **Scheduled day** under [Schedule](definitions.md#schedule)). It is null, not zero, in a month with no scheduled day. Months without a finished race still appear.
- **Scheduled races per hour** counts the counted slots that are scheduled races, by their Eastern Time start hour, and how many started. The hourly rows have `scheduled_count` and `started_count`. The start rate is `started_count / scheduled_count`.
- **Median racers per scheduled race** is, for each hour, the median of racers per scheduled race over that hour's scheduled races, never-started ones counting 0. `q1_racers` and `q3_racers` are the quartiles of the same values. All three are null when nothing was scheduled in the hour. A median of 0 is real: fewer than half of that hour's scheduled races started.
- **Weekday-hour cells** are the same counts for each of the 168 weekday and Eastern hour combinations: `scheduled_count`, `started_count`, `entrant_total` and `average_racers`, which is `entrant_total / scheduled_count`, or null when nothing was scheduled.
- **Bands** colour the weekday-hour cells, computed separately for all data and for each quarter:
  - Only cells with at least 5 scheduled races (`BAND_MIN_SCHEDULED`) are banded and count toward the quartiles.
  - `q1` and `q3` are the quartiles of those cells' `average_racers`.
  - A cell is `prime` if its `average_racers` is at least `q3`, else `quiet` if it is at most `q1`, else `normal`. A tie at a quartile goes to `prime` or `quiet`.
  - A cell with fewer than 5 scheduled races has no band, and when no cell qualifies none has one.
- **Medians and quartiles** describe the observed distribution. Quartiles are the median of each half of the sorted values, leaving out the middle value when the count is odd. The 95% ranges are normal confidence intervals for the mean using the sample standard deviation, and the ranges for entry counts never go below zero. A group with fewer than two values has no range.
- **Diagnostics** report `diagnostics.schedule`: `scheduled_count`, `started_count`, `pulled_count`, and `unscheduled_race_count`, the included races up to **counted through** that no slot became.

Some of this is deliberate behaviour of the data, not a defect:

- **Thin quarters.** A partial quarter, the current one included, has few scheduled races per cell, so most of its cells are unbanded until about mid-quarter. All data and full quarters are fully banded.
- **A sawtooth in the hourly counts.** Some hours hold markedly fewer scheduled races than their neighbours, because seasons alternate their hour grids.
- **Overnight medians.** The median racers per scheduled race can be 0 in the overnight hours, because fewer than half of those scheduled races start.
- **Before the schedule.** A race that started before the first slot joins no slot, so the schedule panels leave it out. The first such races, in April 2025, are in the monthly turnout but not in a per-day value.

## Season locking

[Season ratings](../README.md#season-ratings) are locked once the season has ended and none of its races awaits recording, so they never change again, even if the rating model does. `python3 scripts/build_stats.py --lock-seasons` builds as usual and then locks every season without a ledger, once all four hold:

1. The next season started at least a day ago (`SETTLE`).
2. At least 10 slots follow the next season's start (`LOCK_CONFIRM_SLOTS`), and none of them is the season's own. A misfiled slot that put the next season's start too early would be followed by the season's own slots, so it can never fix a wrong boundary: the season stays open.
3. The archive holds a race that started after the next season's start, so the race sync has covered the season's end.
4. Every race of the season is settled (`race_archive.is_settled`): recorded, unrecordable, or older than the 14 days after which the archive stops refreshing it. A race that was never recorded cannot block a lock forever.

Only the data bot passes `--lock-seasons` (see [Scheduled data refresh](operations.md#scheduled-data-refresh)). A plain build never locks. The build prints `Locked the ratings of season N.` or `Season N stays open: <reason>.` for every season without a ledger.

## Season ledgers

`season-ratings/season-N.json`, beside `archive/` and outside `site/`, holds one locked season: `{schema_version: 1, season, start, end, rating, races}`.

- **Contents:** `start` and `end` are the season's span in UTC, and `rating` is the parameters at lock time. Each race is `{r, mode, e}`: the race slug, the mode name, and `e`, a list of `[user ID, season points, season-mode points]` in entry order. It is keyed by slug, user ID and mode name, never by indexes, so it survives a rebuild that renumbers races and racers.
- **Deterministic:** it holds no wall-clock time, so two locks of the same archive write identical bytes.
- **Written once:** it is created exclusively and never overwritten. Deleting a ledger is the only way to lock that season again, with the rating as it is then.
- **What the build does with it:** a locked season's races and points come from its ledger, whatever the rating computes today. A race the spans place in a locked season that the ledger does not hold, or a ledger race whose mode or entrants no longer match, gets no season and is never re-rated. Both are counted in `diagnostics.seasons` (`left_out_of_locked_seasons`, `mismatched_locked_races`).
- **No visible change:** while the rating is unchanged, locking changes no published byte. Changing the rating changes the open season only.

## Chest counts (CR and CPH)

- **CR** is the number of chests a racer says they opened, and **CPH** is chests per hour: CR × 3600 ÷ finish seconds, rounded to a whole number. Both are soft, self-reported metrics. CR is parsed from the racer's own post-race comment, so it is only as accurate as the comment, and some comments will give a wrong number.
- The build scans each comment for runs of exactly two or three digits. A run is skipped if it:
  - is below 30 or above 300;
  - follows a `/`, with or without a space (the seed's total, as in `164/216`);
  - has `<`, `>` or `+` directly attached on either side;
  - follows `more than`, `less than`, `over`, `under` or `sub` as a whole word, with or without a space (`sub100`);
  - is followed by `CPH`, or by `%` or a time unit (`m`, `min`, `h`, `hr`, `s`, `sec` and their longer or plural forms);
  - is part of a clock time such as `1:45`.

  A `-` does not skip a number.
- CR is the first remaining number whose CPH falls between 30 and 175, inclusive. If there is none, the entry has no CR. Only entrants with status `done` and a finish time can have one, so a comment on a DNF or DQ is never parsed.
- A comment may name several numbers, and the first plausible one wins, so `200 check 199/216` reads as 200. A number like the 50 in `50/50` or the 75 in `19.75` can also pass when it fits the pace range.
- The parsed CR is stored in `comments.json`, and CPH is computed in the browser from the finish time in `participants.json` (see [Latest results](dashboard.md#latest-results)).
