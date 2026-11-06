# racetime.gg API notes

The parts of the public racetime.gg API this repo reads. The full reference is the racetime.gg wiki page [Public API endpoints](https://github.com/racetimeGG/racetime-app/wiki/Public-API-endpoints).

## General

- `GET` only, no authentication, JSON responses, every time in UTC (ISO 8601 dates and durations).
- URLs in responses are relative to `https://racetime.gg`. Avatars are `/media/...` paths or absolute URLs.
- Responses are cached server-side. Cloudflare may answer too many requests with a challenge page, which is why every sync is paced and retried (see README › How a sync works).

## Endpoints used

| Endpoint | Script | Use |
|---|---|---|
| `alttpr-ladder/races/data?show_entrants=true&page=N&per_page=100` | `race_archive.py` | Past races, finished and cancelled, newest `ended_at` first, with full entrant data. |
| `alttpr-ladder/leaderboards/data` | `race_archive.py` | Current display names and avatars from `leaderboards[].rankings[].user`. |
| `user/<id>` (an HTML page, not an API) | `racer_bios.py` | The profile bio, the text of the first `<p class="bio">`. |

## Race fields read

- `name`: `<category>/<slug>`, unique.
- `status.value`: `open`, `invitational`, `pending`, `in_progress`, `finished`, `cancelled` or `partitioned`.
- `info`: the goal text, `info_bot` then `info_user`; the mode, the preset and the seed link are read from it.
- `opened_at`, `started_at`, `ended_at`, `cancelled_at`: a race cancelled before it started has no `started_at` or `ended_at`; one cancelled after has `ended_at` equal to `cancelled_at`.
- `entrants_count`: every entrant, including DNF and DQ.
- `recorded`, `recordable`: whether a moderator recorded the race, and whether it can still be recorded. They decide when a race is settled.
- `entrants`: ordered by status, then place.

## Entrant fields read

- `user`: `id` (stable, alphanumeric), `name`, `avatar` (or `null`). `full_name`, `discriminator`, `pronouns` and the Twitch fields exist but are never published.
- `status.value`: `done`, `dnf`, `dq`, plus pre-race values such as `ready`.
- `finish_time` (ISO 8601 duration or `null`), `place` (integer or `null`).
- `comment`: the post-race remark, or `null`.
- `score` and `score_change` are racetime.gg's own rating; they are archived but never published.
