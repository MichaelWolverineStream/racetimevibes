# Published datasets

- **Purpose:** what `build_stats.py` publishes in `site/data/`, the schedule and participation metrics behind the Ladder data panels, how season ratings are locked into ledgers, and how chest counts are parsed from comments.
- **Read before:** changing a field of `race-stats.json`, `participants.json` or `comments.json`, the season lock, or the chest-count parser. A new field must fit [Privacy](../README.md#privacy), and a schema change bumps the version in Python and in JS together.
- **Code:** `scripts/build_stats.py`, `scripts/participant_stats.py`, `scripts/comment_stats.py`, `scripts/season_ratings.py`, `scripts/season_names.py`, `scripts/badge_overrides.py`. The rating itself is in the [README](../README.md#ladder-rating), and tags, badges and records are in [highlights.md](highlights.md).

## The three files

`build_stats.py` builds three datasets from the archive and writes each one atomically, minified:

| File | Contents |
|---|---|
| `site/data/race-stats.json` | The anonymous aggregate behind the Ladder data panels (see [Schedule and participation metrics](#schedule-and-participation-metrics)). |
| `site/data/participants.json` | The per-racer dataset: races, racers, ratings, highlights and the season fields, listed below. |
| `site/data/comments.json` | The racers' post-race comments, each with the chest count parsed from it (see [Chest counts](#chest-counts-cr-and-cph)). The page fetches it after its first render, so it never delays the board. |

`participants.json` holds:

- **Races:** each carries its Eastern Time date `d`, its start time `u` in Unix seconds, its seed preset, its derived tags and its season (see [Season fields](#season-fields)).
- **Badge definitions:** each carries `repeatable`.
- **Racers:** each carries an overall rating (points, $\mu$, $\sigma$ and finishes) and one for each mode they have raced (points and finishes), badges, their current streaks and a claim to fame, plus:
  - `badge_counts`, `{badge id: times earned}`, for every [repeatable badge](highlights.md#badges) they hold;
  - `badge_notes`, `{badge id: note}`, when a [badge given by hand](highlights.md#badges-given-by-hand) has a note;
  - on their leaderboard row, `races`, `wins` (first places), `diamond`, `gold`, `silver`, `bronze`, `podiums` (the medal total), `dnf`, `dq` and `median_seconds`.
- **`medal_tiers`:** the [medal](definitions.md#entrants) rule.
- **`avatar_changes`:** the racers whose avatar changed (see [Avatar changes](avatars.md#avatar-changes)).
- **`featured`:** the coming [featured racers](highlights.md#featured-racer), one per Eastern date from the newest race's date through 15 days after it that has a pick, each as `[date, player, race, tag, bio]`.
  - `races[race].t[tag]` is their best highlight.
  - `bio` is their current racetime.gg bio or `null`, with whitespace runs collapsed to single spaces and cut to 160 characters with "…", at its last word break if that keeps at least half of it.

`race-stats.json`, `participants.json` and `comments.json` each carry a `schema_version`, and the page refuses data whose version it does not expect: it shows "could not be loaded" for the first two, and leaves out the comments and chest counts for `comments.json`. `site/avatars/` holds the mirrored avatar images (see [Avatar mirror](avatars.md#avatar-mirror)). What each of these may contain is fixed by [Privacy](../README.md#privacy).

### Season fields

The season names come from `scripts/season_names.py`, which holds the retro names, and from `season-names.json` at the repository root: a hand-maintained input, `{schema_version: 1, seasons: {"11": {"animal": "Panda"}}}`. The other one is `badge-overrides.json` (see [Badges given by hand](highlights.md#badges-given-by-hand)). `season_names.load` raises `SeasonNamesError`, failing the build, unless:

- The file has exactly the keys `schema_version` (1) and `seasons`.
- Every key is a plain whole number from 11 on, and every entry has exactly the key `animal`.
- Every animal is a non-empty string, at most 40 characters, with no leading or trailing whitespace.
- No two seasons share an animal, ignoring case, and no animal is a retro name, short or full.

The season fields of `participants.json`:

- **`seasons`:** an array, oldest first, of `{n, first, last, ended}` for every season that holds at least one race. `first` and `last` are the Eastern Time dates of the season's first and latest race. `ended` is true once the season is locked or a later season has started. The current season is the last element. The array is empty when there is no schedule, and the page then shows everything all-time.
- **`season_names`:** an object keyed by season number as a string. A retro season (1-10) is `{kind: "retro", text, full}` and a champion season (11 on) is `{kind: "champion", text}`, `text` being the animal. A season with no entry is unnamed. See [Season names](dashboard.md#season-names).
- **`upcoming`:** rows `[t, mode name, mode index, season]`: `t` in Unix seconds, the schedule's mode name, the published mode index or `null`, and the season by the [season rule](definitions.md#seasons) or `null`, never the slot's filed number. They are the first `UPCOMING_SLOTS` (48) schedule slots strictly after the newest included race, so the list hangs on the archive and never the clock, and a rebuild is identical. A slot whose mode the schedule does not list fails the build.
- **`races[].s`:** the race's season number. It is absent for a race with no season.
- **Entries:** `races[].e[k]` is `[player, place, status, seconds, overall_after, mode_after, season_after, season_mode_after]`. `[6]` and `[7]` are integers when the race has `s`, and both `null` otherwise.
- **No per-player season fields.** A racer's standing in a season is derived from their entries: their last `[6]` among the season's races for the overall rating, and their last `[7]` among the season's races in a mode for that mode's rating. Their season finishes are their finisher entries in those races, and they are provisional below 5.
- **`diagnostics.seasons`:** `stray_slots`, `races_without_season`, `left_out_of_locked_seasons` and `mismatched_locked_races`. `races_without_season` counts only races before season 1's span: a race in a [season break](definitions.md#seasons) is excluded, not seasonless.
- `rating`, each player's rating, $\mu$, $\sigma$ and finishes, their mode ratings (points and finishes), and `leaderboards` are all-time.

## Schedule and participation metrics

`build_stats.py` computes these for `race-stats.json`. They drive the [Ladder data panels](dashboard.md#ladder-data-panels), and the slots they count are defined under [Schedule](definitions.md#schedule).

- **Periods** are calendar quarters (`Q1` to `Q4`). A race belongs to the quarter of its Eastern Time start, and a counted slot to the quarter of its own Eastern Time start. Edge periods are marked partial (`is_complete` is false) when the archive does not cover the full quarter. Each period keeps its id, label, start and end, `is_complete`, `observed_local_date_range` and its weekday-hour cells, so its slot counts add up to the overall ones. Seasons are not split by period. A counted slot in a quarter with no finished race is a build error.
- **Race turnout** uses `entrants_count`, which includes repeat participants and entrants who were disqualified or forfeited. It measures race entries, not people. The **season health counts** below measure people.
- **Monthly participation** is the average entries per finished race: total race entries divided by the finished races in that calendar month. A month's finished-race summary also gives the median and the 95% range.
- **Entries per scheduled day** is the entries of that month's finished races that started on scheduled days (`scheduled_day_entrant_total`), divided by the month's scheduled days (`scheduled_day_count`, see **Scheduled day** under [Schedule](definitions.md#schedule)). It is null, not zero, in a month with no scheduled day. Months without a finished race still appear.
- **Races fired per scheduled day** is the month's fired races on scheduled days (`fired_race_count`), divided by its scheduled days (`scheduled_day_count`): `fired_per_day`. A **fired** race is a scheduled race that started: an included race that a slot joined (see **Join** under [Schedule](definitions.md#schedule)). A finished race that no slot joined adds to entries per scheduled day but never to this. It is null, not zero, in a month with no scheduled day. Like entries per scheduled day, it ignores **counted through**.
- **Seasons** is one row per season, oldest first, for every season holding at least one included race or counted scheduled race, as `{season, is_complete, scheduled_count, started_count, real_field_count, race_count, entrant_total, scheduled_day_count, scheduled_day_entrant_total, fired_race_count, fired_entrant_total}` followed by the rest of the season health counts below. A race or slot belongs to the season its own start falls in, by the [season rule](definitions.md#seasons), never the season number it is filed under.
  - `scheduled_count` and `started_count` count the counted slots that are scheduled races in that season, and how many started. The start rate is `started_count / scheduled_count`.
  - `race_count` and `entrant_total` cover every included race in the season, and are not cut at **counted through**.
  - `scheduled_day_count` is the season's scheduled days. `scheduled_day_entrant_total` is the entries of the season's included races that started on one of them. `fired_race_count` is those races that a slot joined, and `fired_entrant_total` is their entries. They mirror the monthly per-day numbers and ignore **counted through**.
  - A scheduled day belongs to the season of each of its slots, and a race counts only on a scheduled day of its own season. A race in a season's span on a day without one of its slots, such as one just after midnight within 30 minutes of its last slot, counts toward the season but toward none of its per-day fields. A race in a [season break](definitions.md#seasons) is not included at all.
  - A changeover day that holds the last slots of one season and the first slot of the next is a scheduled day of both, so the seasons' scheduled days can add up to more than the months'.
  - The page computes entries per day, fired races per day and racers per fired race from these counts. Each is null, shown as a dash, when its denominator is 0.
  - `is_complete` is true once the next season started on or before **counted through**, so all of the season's slots and races are in. The newest season is normally in progress.
- **Season health counts** are `real_field_count` and the fields after `fired_entrant_total` in each `seasons[]` row, behind the Participation panel (section 05, Season health). They are counts of people and races, never ids, built by `_season_summary`. They count people instead of entries, compare between seasons of different length, and cover reach, race quality and renewal. See [Seasons](definitions.md#seasons) and **Racer of a season** under [Entrants](definitions.md#entrants).
  - `racer_count` is the season's distinct racers, by the season of each race under the [season rule](definitions.md#seasons), not the locked ledgers.
  - `real_field_count` counts the season's counted, unpulled slots that are a **Real field** race (see [Races](definitions.md#races)). The share of races with 4+ racers divides it by `scheduled_count`, so a slot that never started counts against it.
  - `season_day_count` is the season's **days counted**, and `racer_week_total` adds up the distinct racers of each of its **season weeks** (see [Seasons](definitions.md#seasons)), the partial last week included. A racer counts once for each week they raced in. The page computes **active racers per week** as `racer_week_total / (season_day_count / 7)`, null while no day is counted.
  - `full_week_count` counts the season's full season weeks, and `weekly_racers_min` and `weekly_racers_max` are the fewest and most distinct racers in one of them. They are null while the season has no full week.
  - `previous_racer_count` is the racers of the season before, and `returning_count` how many of them raced this season. Both are null for a season with no racers before it, the first.
  - `newcomer_count` is the season's newcomers, `newcomer_matured_count` how many have matured, and `newcomer_retained_count` how many of those raced again (see **Newcomer** under [Seasons](definitions.md#seasons)).
  - `REAL_FIELD_MIN` (4), `SEASON_WEEK_DAYS` (7) and `NEWCOMER_WINDOW_DAYS` (28) are set in `build_stats.py`, and all three are mirrored in `site/app.js`.
- **Weekday-hour cells** hold, for each of the 168 weekday and Eastern Time hour combinations, in the overall data and in each period: the counted slots that are scheduled races (`scheduled_count`), how many started (`started_count`) and `average_racers`, the entries of those races divided by `scheduled_count`, or null when nothing was scheduled.
- **Bands** colour the weekday-hour cells, computed separately for all data and for each quarter:
  - Only cells with at least 5 scheduled races (`BAND_MIN_SCHEDULED`, mirrored in `site/app.js` and the heatmap legend in `site/index.html`) are banded and count toward the quartiles.
  - `q1` and `q3` are the quartiles of those cells' `average_racers`.
  - A cell is `prime` if its `average_racers` is at least `q3`, else `quiet` if it is at most `q1`, else `normal`. A tie at a quartile goes to `prime` or `quiet`.
  - A cell with fewer than 5 scheduled races has no band, and when no cell qualifies none has one.
- **Medians and quartiles** describe the observed distribution. Only the medians are published; the quartiles set the bands. Quartiles are the median of each half of the sorted values, leaving out the middle value when the count is odd. The 95% ranges are normal confidence intervals for the mean using the sample standard deviation, and the ranges for entry counts never go below zero. A group with fewer than two values has no range.
- **Diagnostics** report `diagnostics.schedule`: `scheduled_count`, `started_count`, `pulled_count`, and `unscheduled_race_count`, the included races up to **counted through** that no slot became. `diagnostics.seasons` reports `races_without_season` and `slots_without_season`: the included races and counted scheduled races before season 1's span, which the `seasons` rows leave out. `diagnostics.excluded_in_season_breaks`, an integer beside `excluded_by_mode`, counts the races excluded for starting in a [season break](definitions.md#seasons); they are also in `source.excluded_archived_race_count`.

Some of this is deliberate behaviour of the data, not a defect:

- **Thin quarters.** A partial quarter, the current one included, has few scheduled races per cell, so most of its cells are unbanded until about mid-quarter. All data and full quarters are fully banded.
- **Before the schedule.** A race that started before the first slot joins no slot, so the schedule panels leave it out. The first such races, in April 2025, are in the monthly turnout but not in a per-day value. Those April 2025 races have no season either, so the season rows leave them out (`races_without_season`).
- **Season 1's newcomers** (185) are a data-start effect: the archive begins just before it.
- **A young season's returning share** starts low because the season is young, not because racers left. Season 11 stood at about half on day 6, where earlier seasons stood at 35-57% on their day 6 and ended at 67-90%.
- **Season breaks.** A race more than 30 minutes after a season's last slot and more than 5 minutes before the next season's first slot is in a [season break](definitions.md#seasons) and in no dataset, such as the two races on 2026-11-06 between seasons 10 and 11.
- **A young season's active racers per week** starts high. Most of a week's racers race in its first few days, so a partial week's racers, spread over only those days, overstate a whole week. Season 10 read 385 on its day 1, 208 on day 3 and 125 on day 7, and ended at 116. The value rises briefly again as each new week starts, less so as the season grows.
- **Season 10's last week.** Season 10's season days start at 09:55 ET, and its span ends at 23:30 ET on 2026-11-05, in the season day that ends at 09:55 ET on Nov 6. Its ninth week (Nov 2-9) is partial. Its four days count toward active racers per week, but not toward the weekly range: it has 8 full weeks.
- **Season days lag little.** A season day counts toward active racers per week, and a full week toward the weekly range, at the first build after a race starts later than that day's end. With races every few hours, that is the next bot run. The schedule panels still follow **counted through** and lag about two days.
- **A misfiled slot.** A slot filed under a later season but dated before season 1 has no season by the [season rule](definitions.md#seasons). It counts in the heatmap and in `slots_without_season`, but in no season row.

## Season locking

[Season ratings](../README.md#season-ratings) are locked once the season has ended and none of its races awaits recording, so they never change again, even if the rating model does. `python3 scripts/build_stats.py --lock-seasons` builds as usual and then locks every season without a ledger, once all four hold:

1. The next season started at least a day ago (`SETTLE`).
2. At least 10 slots follow the next season's start (`LOCK_CONFIRM_SLOTS`), and none of them is the season's own. A misfiled slot that put the next season's start too early would be followed by the season's own slots, so it can never fix a wrong boundary: the season stays open.
3. The archive holds a race that started after the next season's start, so the race sync has covered the season's end.
4. Every race of the season is settled (`race_archive.is_settled`): recorded, unrecordable, or older than the 14 days after which the archive stops refreshing it. A race that was never recorded cannot block a lock forever.

Only the data bot passes `--lock-seasons` (see [Scheduled data refresh](operations.md#scheduled-data-refresh)). A plain build never locks. The build prints `Locked the ratings of season N.` or `Season N stays open: <reason>.` for every season without a ledger.

## Season ledgers

`season-ratings/season-N.json`, beside `archive/` and outside `site/`, holds one locked season: `{schema_version: 1, season, start, end, rating, races}`.

- **Contents:** `start` is the season's start and `end` the next season's start, in UTC, and `rating` is the parameters at lock time. Each race is `{r, mode, e}`: the race slug, the mode name, and `e`, a list of `[user ID, season points, season-mode points]` in entry order. It is keyed by slug, user ID and mode name, never by indexes, so it survives a rebuild that renumbers races and racers.
- **Deterministic:** it holds no wall-clock time, so two locks of the same archive write identical bytes.
- **Written once:** it is created exclusively and never overwritten. Deleting a ledger is the only way to lock that season again, with the rating as it is then.
- **What the build does with it:** a locked season's races and points come from its ledger, whatever the rating computes today, for the races the build includes. A ledger race in a season break is excluded and appears nowhere: season 10's ledger still lists the two 2026-11-06 break races `wonderful-bugnet-5002` and `scruffy-structuralmike-9862`. Season 10 is deliberately not relocked. A race the spans place in a locked season that the ledger does not hold, or a ledger race whose mode or entrants no longer match, gets no season and is never re-rated. Both are counted in `diagnostics.seasons` (`left_out_of_locked_seasons`, `mismatched_locked_races`).
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
