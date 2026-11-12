# The race archive

- **Purpose:** what `archive/` holds and how each sync fills it: races, racer names, avatars, bios and the ladder schedule mirror.
- **Read before:** changing a sync script, or anything that reads `archive/`.
- **Code:** `scripts/race_archive.py`, `scripts/racer_bios.py`, `scripts/schedule_archive.py`. The racetime.gg endpoints and fields they read are listed in [racetime-api.md](racetime-api.md).

`archive/` is a verbatim mirror. It is never edited by hand and never deployed; what it holds, and why, is under [Privacy](../README.md#privacy).

## Files

`archive/races/alttpr-ladder/<slug>.json` holds one file per race, named after the race. Each file stores the race exactly as the API's past-races listing returned it, with entrant details, inside an envelope that records when it was fetched:

```json
{"schema_version": 1, "fetched_at": "2026-10-27T10:59:46Z", "race": {"name": "alttpr-ladder/dazzling-matt-4030", "...": "..."}}
```

- The archive covers every race in the category from 2025-04-20; the builder drops only the `ladder_test` mode.
- `archive/racer-names.json` maps each racer's stable user ID to their current display name.
- `archive/racer-avatars.json` maps it to the avatar URL (or `null`) the category leaderboards last showed for them, with the time the sync first saw that value.
- `archive/racer-bios.json` maps it to their bio and its history, written by `racer_bios.py`, not by the race sync (see [Bios](#bios)).
- `archive/ladder-schedule/` holds the alttpr.racing schedule (see [Ladder schedule mirror](#ladder-schedule-mirror)).
- Every archive file is minified JSON: no whitespace between tokens, and one trailing newline.

## How a sync works

The listing is sorted by completion time, newest first, 100 races per page. A sync reads it backwards from page one and stops once it passes a horizon. The horizon is one day before the newest archived race, or the oldest archived race still waiting for a moderator to record it, whichever is earlier. A normal sync is one request for page one plus one for the category leaderboards. With an empty archive the sync reads every page, so the first run is a full backfill. Transient network failures are retried with bounded backoff, and the sync checks completion-time ordering and race names before writing anything.

Finished races still change until a moderator records them, usually within two days: `recorded` flips, `score_change` is filled in, and racers add comments. So a race's file is rewritten while the race is unrecorded and its data changes. Once the race is recorded, is marked unrecordable, or finished more than 14 days ago, its file is never touched again. Files are written oldest first, so an interrupted sync leaves no gap behind the newest archived race.

If the archive ever holds fewer races than the API reports, the sync reads every page once to fill the gap, and fails if races are still missing. If an unrecorded race disappears from the listing, its last archived copy is kept and the sync prints a warning.

## Names and avatars

User IDs are stable, but display names can change, and each race file keeps the name a racer had when it was fetched. Each sync merges the names from the category leaderboards into `racer-names.json`; the leaderboards cover nearly every racer with a recorded race. The dashboard shows a racer under that name, or, if they have no entry, under the name on their most recent race.

Avatars work the same way but never cost a request. The leaderboards response already carries each ranked racer's current avatar, so each sync merges it into `racer-avatars.json` as `[avatar URL or null, first-seen time]`, where the time is when the sync received the leaderboards.

- A user the leaderboards list without an `avatar` field is left as they were, not cleared.
- A racer's entry keeps its time while the value stays the same, and entries are never removed.
- The file is rewritten only when a value is added or changes, so an ordinary sync leaves it untouched.
- A racer who uploads a new picture gets a new URL, so the change is picked up at the next sync even if they have not raced. A change and a revert to the same file name within a single sync interval goes unnoticed.

How the published avatar is chosen and mirrored is in [avatars.md](avatars.md).

## Bios

A racer's bio is on their public profile page, `https://racetime.gg/user/<id>`, and in no JSON API, so `racer_bios.py` reads the page's HTML. It stores the text of the first `<p class="bio">`, links included, stripped and cut to 2,000 characters, the length the field allows. A profile with no bio, or an empty one, is stored as `null`. Entries are `[bio, checked-at time, since, earlier]`, with times in UTC, sorted by user ID.

- **Bio:** the text or `null`. **Checked-at time:** when the profile was last fetched.
- **Since:** when the current bio was first seen. **Earlier:** the racer's earlier bios, oldest first, each `[bio, since]`. A fetch that finds a different bio moves the old one, with its time, to the end of `earlier`; one that finds the same bio changes only the checked-at time. A racer's first fetch is not a change.
- The history began on 2026-11-05, so an entry stored before then has an unknown `since` and no earlier bios. Only the first earlier bio, or `since` when there are none, can be unknown (`null`). Any other entry shape is refused.
- Earlier bios are kept so a change of [favourite Pokémon](avatars.md#pokémon-avatars) can be dated (see [Avatar changes](avatars.md#avatar-changes)).

The sync:

- **Who is due:** a racer of an included race (see [Definitions](definitions.md#races)) with no stored entry, or who entered an included race that finished after the time of their entry. A racer who has not raced since their bio was checked costs nothing, so a normal daily run fetches only the racers who raced that day.
- **Pacing:** one request per racer, 1 second apart, with the same bounded retries as the race sync. A page is read up to 1,000,000 bytes, and a longer one is a failure.
- **A 404** means a hidden or deleted profile. It is stored as `null`, so the racer is not asked for again until they race again.
- **A failure** is any other HTTP error, network error, oversized page, or user ID that is not letters and digits (never put into a URL). It prints a `Warning:` line and keeps the racer's old entry, so they are due again on the next run.
- **Early stop:** after 5 failures in a row the run stops with a warning, because racetime.gg is refusing profile pages. The racers not yet reached stay due. A success resets the count. The script still exits 0, so the rest of the refresh goes on with the bios it has.
- **Challenge pages:** a page without a `user-profile` block, such as a challenge or error page, is a failure. It never becomes a stored `null` bio.
- **The file** is rewritten only when at least one racer was checked, so a run that finds nobody due changes nothing.
- **Output:** the script ends with `Bios: N checked, K failed; M name a favourite Pokémon.`, where `M` counts the stored bios that name one, `random` included. It exits with 1 only if the archive cannot be read. If the pokédex cannot be read, it prints a `Warning:` line and leaves the count out, because the bios are already saved.

## Ladder schedule mirror

`scripts/schedule_archive.py` mirrors the alttpr.racing ladder schedule, so the build and the [schedule planner](analyses.md#race-schedule-proposals) can see every slot, including the rooms that were cancelled and so never reached racetime.gg. Every bot data run syncs it before the build (see [Scheduled data refresh](operations.md#scheduled-data-refresh)), and `build_stats.py` reads it and fails without it.

- **What it fetches:**
  - `https://alttpr.racing/api/v1/modes`, the modes with their archetype, name and `active` flag;
  - `https://alttpr.racing/api/v1/schedule/<n>` for seasons 1, 2, …, stopping at the first season that answers 404. Season 0 is never fetched: it is an alias of the current season, with local-time strings.
- **Where it writes:** `archive/ladder-schedule/modes.json` and `archive/ladder-schedule/season-<n>.json`. Each file wraps the response verbatim as `{"schema_version": 1, "fetched_at": …, "modes" | "schedule": [...]}`, minified with one trailing newline.
- **Validation:** every response is validated before any file is written. A slot needs integer `id`, `season` and `mode`, a `raceId` that is an integer or null, a `time` of digits (Unix seconds), and its file's season. A mode needs integer `id` and `archetype`, a string `name`, and a boolean `active`. Any failure writes nothing.
- **Rewrites:** a file is rewritten only when its body changed, so a run without news changes nothing. Files are never deleted.
- **Reading it back:** `load_schedule` returns every slot once by its `id`; the same id with different content is an error.
- **Seasons:** `season_starts`, `season_of` and `stray_slots` also live in this module, for the rules under [Seasons](definitions.md#seasons).
- **The join:** `join_races` also lives in this module. It matches slots to included races by the rules under [Schedule](definitions.md#schedule), for the build and the planner alike.
- **Race ids arrive late.** alttpr.racing fills in a slot's `raceId` only when the room is created. A mirror is therefore only as current as its last sync, and the build counts slots only up to the latest one with a `raceId` (see **Counted through** under [Schedule](definitions.md#schedule)).
- **Output:** the script ends with `Schedule: N seasons, M slots, K files changed.` and exits with 1 on any failure.
- **Privacy:** the schedule holds slot times, mode ids and alttpr.racing race ids, nothing about racers.
