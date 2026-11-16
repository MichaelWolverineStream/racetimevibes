# Definitions

- **Purpose:** the shared terms. The README and every doc use them with exactly these meanings.
- **Read before:** changing which races count, how a race's mode, preset, date or season is derived, or how schedule slots join races.
- **Code:** `scripts/build_stats.py` (races, modes, presets), `scripts/highlights.py` (`MEDAL_TIERS`), `scripts/schedule_archive.py` (`join_races`, `season_starts`, `season_spans`, `season_of`, `in_season_break`).

## Races

- **Included race:** only records whose `status.value` is `finished` and that are not in a [season break](#seasons) are included. The datasets cover every archived finished race, beginning 2025-04-20.
- **Excluded mode:** races whose normalized `info` mode is `ladder_test` or `ladder test` are excluded from every metric. So are races in a [season break](#seasons).
- **Mode:** the text in the brackets of the `Step Ladder Series - [mode]` prefix of `info`, trimmed and lowercased. Later tags such as `[VT]` are ignored. A missing or malformed prefix is grouped as `unknown`.
- **Preset:** the name after the mode in the race's `info` first line, trimmed, cut at any seed URL (along with a room slug such as `owner/slug` just before it), with `[x]` turned into `(x)` and any `Grab Bag (...)` collapsed to `Grab Bag`. Each preset belongs to its mode.
- **Field:** the race's entrant count, which includes DNFs and anonymous entrants.
- **Real field:** a scheduled race that started with a field of at least 4 (`REAL_FIELD_MIN`), the smallest field that awards gold and silver (see **Medal** under [Entrants](#entrants)).

## Time

- All dates, hours and schedule buckets use `America/New_York` (Eastern Time), including the seasonal switch between EST and EDT. A race's **date**, or **day**, is its Eastern Time local start date.
- The one exception is display: the dashboard shows the start times in Upcoming races and Latest results, and the Latest results date headings, in the viewer's own time zone (see [Viewer time zone](dashboard.md#viewer-time-zone)).
- A **week** runs Monday to Sunday. A **season week** is the exception: seven days counted from the Eastern Time date of the season's start (see [Seasons](#seasons)).

## Entrants

- **Identified:** an entry carrying a stable public user ID. Anonymized entries are not identified.
- **Racer of a season:** an identified entrant, of any status, with an entry in an included race of the season, counted as one distinct person. Anonymous entries count in a race's field, never as a racer.
- **Finisher:** an entrant whose status is `done` and who has a `place`. A **timed** finisher also has a `finish_time`. Everyone else, DNF or DQ, is a non-finisher.
- **Winner:** the identified entrant with place 1.
- **Medal:** the reward a finisher's place earns in its race, set by the race's field (see [Races](#races)). The rule is `MEDAL_TIERS` in `scripts/highlights.py`:

  | Field | Medals |
  |---|---|
  | 2–3 | 1st gold |
  | 4–5 | 1st gold, 2nd silver |
  | 6–9 | 1st gold, 2nd silver, 3rd bronze |
  | 10+ | 1st diamond, 2nd gold, 3rd silver, 4th bronze |

  A field of 1 earns no medal.
- **Podium:** a finish that earns a medal.
- **Provisional:** a racer with fewer than 5 finishes, in any mode, before a given race. A mode rating has its own provisional rule (see [Mode ratings](../README.md#mode-ratings)).

## Schedule

These terms use the [ladder schedule mirror](archive.md#ladder-schedule-mirror). `schedule_archive.join_races` implements the join, and the build and the schedule planner share it.

- **Slot:** one row of the mirrored schedule. Its start is its `time`, in Unix seconds. Slots are taken in `(time, id)` order.
- **Join:** a slot becomes an included race in one of two ways, tried in this order.
  1. The slot's `raceId` matches the number in an included race's `getseed.php?race=<n>`.
  2. Failing that, it matches an included race without such a number. The race must be in the category of the slot mode's archetype and start between 5 minutes before and 30 minutes after the slot (`JOIN_EARLY` and `JOIN_LATE`). Each such race matches once.
- **Category of an archetype:** `ARCHETYPE_CATEGORIES` maps an archetype to a race mode: 1 simple, 2 entrance, 3 inverted, 4 keys, 5 glitched, 6 fun, 7 mystery, 9 spoiler. **Archetype 8** (modes 20 to 24) has no category, so its slots join only by seed. That is not an error in the build.
- **Slot outcomes:**
  - A slot whose `raceId` belongs to a race excluded for its mode (`ladder_test`) is dropped.
  - A slot with a null `raceId` and no race is **pulled**: it was removed from the schedule.
  - Every other slot is a **scheduled race**. It **started** if it joined a race. Otherwise it **never started**, and it counts as **0 racers**.
- **Racers per scheduled race:** the joined race's entrant count, which is the field (see [Races](#races)), or 0 for a scheduled race that never started.
- **Counted through:** a slot counts in the schedule panels only when it starts on or after the archive's first date (`data_start_local`, Eastern Time), and no later than the earlier of two times:
  - one day before the start of the newest included race (`SETTLE`), because rooms may still be open;
  - the start of the latest slot in the mirror that has a `raceId`.

  The second time is a staleness guard: alttpr.racing fills in a `raceId` only when a room is created, so a stale mirror must never make recent slots look pulled. The bot syncs the mirror on every data run, so the first time is almost always the earlier one, and the guard bites only when a schedule sync fails or someone builds with an old mirror. When no slot has a `raceId`, nothing counts. The result is published as `source.schedule_counted_through` and shown on the page.
- **Scheduled day:** an Eastern Time date inside the archive's dates that holds at least one slot. Pulled slots count. Slots of `ladder_test` races do not. The days that are not scheduled are the gaps between seasons and the time before the first slot, so April 2025 has none.
  - A scheduled day belongs to the season of each of its slots, by the [season rule](#seasons), so a changeover day can belong to two seasons. A day whose slots fall in no season is a scheduled day for the monthly numbers and belongs to no season.
  - Scheduled days deliberately ignore **counted through**. The schedule is published ahead, so whether a day has races is known before its outcomes are, and the latest days stay in the per-day averages.
  - A season the mirror does not list yet only delays its days. Their entries leave the numerator too, so the average is never inflated.
- **Last whole day:** the Eastern Time date of **counted through**, minus one day.
- **Seasons by start time:** the per-season panels place a slot in the season its own start falls in, by the [season rule](#seasons), never by the season number it carries. API seasons overlap, so the filed number says little about when a slot ran. Every other panel buckets a slot by its own Eastern Time start.

## Seasons

These terms drive the [season ratings](../README.md#season-ratings). `schedule_archive.season_starts`, `season_spans`, `season_of` and `in_season_break` implement them.

- **Season start:** season 1 starts at its first slot. Every later season starts at its first slot after the middle of the season before it, which is the later of that season's start and the median time of its slots. A season with no slot after that point has no start yet. The mirror holds a season's slots, future ones included, as soon as alttpr.racing publishes its schedule, so a season's last slot, and with it the end of its span, is known ahead.
  - A race's season comes from this rule, not from the season number its slot carries. alttpr.racing files the odd slot under the wrong season, such as an old season-1 slot listed inside a later season, and the rule keeps such a slot from moving a boundary. `diagnostics.seasons.stray_slots` counts the slots outside their own season's run.
- **Season:** the span from 5 minutes (`JOIN_EARLY`) before its start to 30 minutes (`JOIN_LATE`) after its **last slot**, inclusive (`SeasonSpan`, from `season_spans`). A race belongs to the season whose span holds its start, scheduled or not. Where two spans overlap, the later season wins.
  - A season's **last slot** is the latest slot filed under it from its start until the next season's start (for the newest season, from its start on), so a misfiled slot never moves an end.
  - Races before season 1's span have no season but are included everywhere else, and `diagnostics.seasons.races_without_season` counts only them.
- **Season break:** a start after season 1's span opens that no season's span holds. A race in a season break is excluded from everything, like the **Excluded mode**: every rating, season and all-time stats, records, comments, racer pages, bios and avatars. `diagnostics.excluded_in_season_breaks` and `source.excluded_archived_race_count` count it.
- **Current season:** the newest season holding at least one included race.
- **Season week:** seven days counted from the Eastern Time date of the season's start. The season's last day is the earlier of the Eastern Time date of its **last slot** and the **last whole day**. A season week is **full** when it ends on or before that day. A partial last week is left out: its races count toward the season's racers but toward no week.
- **Returning racer:** one of the previous season's racers who is also one of this season's. The first season has none, because no season comes before it.
- **Newcomer:** a racer whose first included race falls in the season. A racer who first raced before season 1 is no season's newcomer. A newcomer has **matured** when the 28 days (`NEWCOMER_WINDOW_DAYS`) after the date of their first race end on or before the **last whole day**, and is **retained** when they raced on a later date within those 28 days. That race may be in the next season.
