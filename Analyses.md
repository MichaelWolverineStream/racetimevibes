# Standalone analyses

Four scripts in `scripts/` analyse the archive on their own. The dashboard build never runs them, and they change nothing in `site/`. The dashboard itself is specified in [README.md](README.md).

## Mode overlap

`scripts/mode_overlap.py` measures how much the modes share racers, to find modes that could run in the same slot without making racers pick one. It is a standalone analysis: the dashboard build does not run it, and it writes nothing unless asked. `--html` writes a self-contained report page, with no scripts or external requests, in the claude.ai Artifact page format, which leaves the document skeleton to the publisher.

```bash
python3 scripts/mode_overlap.py                  # ranked pair table
python3 scripts/mode_overlap.py --min-entries 2  # count only racers with 2+ entries in the other mode
python3 scripts/mode_overlap.py --html report.html
```

### In plain words

For a pair of modes A and B, look only at the months when both were on the schedule, and ask: what share of A's entries came from racers who also raced B that month? If A ran against B, that share of A's field would have to choose. The pair's score is the larger of the two directions, because a pairing is only as good as its effect on the mode that loses more.

### Definitions

- Races are the dashboard's included races (see [README › Definitions](README.md#definitions)), and entrants are identified by stable user ID; anonymous entries are skipped. Any entry counts, DNFs and DQs included, because entering is what a clash takes away.
- Months are Eastern Time calendar months. A mode is **on the schedule** in a month when it has at least 4 races that month. A pair's **co-active months** are the months when both modes are on the schedule, and every pair figure uses only those months.
- The **directed share A→B** is A's entries in the co-active months made by racers who entered B at least once that same month (`--min-entries` raises the once), divided by all of A's identified entries in those months.
- The **chance baseline A→B** is the share expected if A's racers entered B at B's general rate. In each co-active month, B's reach is the racers who met the same entry threshold in B divided by every racer with any entry that month. The baseline is that reach averaged over the co-active months, weighted by A's entries in each. A share well below its baseline means the two modes draw different crowds; a share near it means B is simply popular.
- The **pair score** is the larger of A→B and B→A. Below 0.25 it rates **low**, below 0.5 **moderate**, and **high** otherwise. A pair with fewer than 6 co-active months is **weak evidence**. A pair with no co-active months is unmeasured.
- The **95% interval** is a percentile bootstrap of the pair score: 1,000 rounds that each draw every racer in the archive with replacement, keeping all of a racer's months together, with a fixed seed so runs repeat exactly.
- The **trend** repeats the pair score within each calendar half-year, over the co-active months in that half-year, without an interval.
- Thresholds are constants at the top of the module.

### Limits

- The score is an upper bound on the conflict. Racing both modes in a month does not prove a racer would want both slots on the same day. The bot never runs two modes at once, so the true forced choice cannot be observed.
- Pairs are measured over different months, so a paused mode such as inverted is compared on the racers of its own era.

## Race schedule proposals

`scripts/schedule_planner.py` proposes one season of ladder races as four sets, *Light*, *Medium*, *Full* and *Bonanza*, from 55 to 76 races a week plus the races that start between them. All four run exactly the categories and modes that the plan file lists. It learns from how many racers came to every slot the alttpr.racing schedule ever held, including the cancelled ones. It is a standalone analysis: the dashboard build does not run it, and it writes nothing unless asked. The rules this section describes are `SHIPPED` in the code, and [Schedule strategy comparison](#schedule-strategy-comparison) tests deviations from them.

It reads the race archive and the schedule mirror (see [README › Ladder schedule mirror](README.md#ladder-schedule-mirror)), so refresh both first. `--out` writes each proposal as CSV and as JSON, and the success rates as CSV. `--html` writes a self-contained report page and `--rates` a self-contained success-rate page. Both are in the claude.ai Artifact page format, with no scripts or external requests. The planner is deterministic: the same archive, plan file and start date always give the same proposals.

```bash
python3 scripts/schedule_archive.py                     # refresh the schedule mirror (network)
python3 scripts/schedule_planner.py                     # the four proposals
python3 scripts/schedule_planner.py --out proposals     # also write CSV and JSON per proposal
python3 scripts/schedule_planner.py --rates rates.html  # also write the success-rate page
python3 scripts/schedule_planner.py --start 2027-01-11 --weeks 9 --html report.html
python3 scripts/schedule_planner.py --config other-plan.json
```

### Terms

This section uses alttpr.racing's words, which differ from the dashboard's:
- A **category** is an alttpr.racing *archetype*. The dashboard calls it a mode: entrance, simple, keys and so on.
- A **mode** is an alttpr.racing *mode*. The dashboard calls it a preset: Crosskeys 2024, AD Keysanity and so on.
- A **plan category** is a key of the plan file's composition. Most plan categories are a category under its own name, but one may mix modes from several, as `throwback` does. The categories of its modes are its **sources**, and the races count under those categories on racetime.gg and in the dashboard.

### In plain words

A slot that never became an archived race was cancelled, because rooms with fewer than two entrants are never archived, so it counts as zero entrants. A race's expected turnout is its category's average in that four-hour part of the day, adjusted for the exact hour of the date's day group and for the mode.

The days fall into three **day groups**:
- **Work**: Monday to Wednesday.
- **Casual**: Thursday and Friday.
- **Prime**: Saturday, Sunday and the common US days off, such as Thanksgiving and Christmas Eve.

The plan file splits each group's hours into **prime time**, **quiet** hours and **normal** hours (Eastern Time, both ends included):

| Group | Prime | Quiet | Normal |
|---|---|---|---|
| Work | 12–15 | 1–7 | the other 13 hours |
| Casual | 12–17 | 2–7 | the other 12 hours |
| Prime | 11–18 | 2–4 | the other 13 hours |

Glitched and Spoiler are **between categories**. They share few racers with the others, and a Spoiler race is short, so their races always start between two other races, never in a slot of their own. All the other categories are **main categories**. Each set runs a fixed number of main races a day in each group, and the between races come on top:

| Set | Work | Casual | Prime | A week |
|---|---|---|---|---|
| Light | 7 | 8 | 9 | 55 |
| Medium | 8 | 9 | 10 | 62 |
| Full | 9 | 10 | 11 | 69 |
| Bonanza | 10 | 11 | 12 | 76 |

Today's grid has 12 races a day, 84 a week. The planner fills the season in four steps:

1. **Featured races.** Each plan category gets one race in prime time, on a weekday of its own, at the same hour every week. The hour is picked from the category's own record at it, never inside a weekly event that rules the category out. A featured Glitched or Spoiler race is a between race like any other.
2. **Hours.** Main races start at least 2 hours apart. Each date takes its main races at the hours that hold the most prime races. Outside prime time, 3 hours apart is preferred: a race goes to a quiet hour before a normal-hour gap shrinks to 2 hours. After that come the most normal races. So the hours shift from day to day, and quiet races do not always start at the same hour.
3. **Categories.** Prime-time races are given out first, then normal, then quiet. Each race goes to the main category furthest below its share of that band. A band is shared by expected turnout. A race avoids the category that ran at the same hour a week earlier, then the one that ran there a day earlier.
4. **Between races.** A between category races whenever it is behind its own turnout share of a band, in a gap between two main races: 1 or 2 hours after one and 1 or 2 hours before the next. A gap holds at most one between race.

**A category never races where it almost never fired.** If a category's rooms fired less than 10% of the time in a band, it never races in that band. Today that keeps Glitched and Spoiler out of quiet hours. A category with `onlyPrimeTime` races only in prime time. A category's prime-time races run its most popular mode, and its other races take its modes in turn.

The report ranks the four proposals by expected entrants per race. A separate **success-rate page** shows how often each category's rooms fired at each weekday and hour, and in each band. It highlights the rates below 20% and marks the bands closed to a category.

### The plan file

`schedule-plan.json` holds the season, the bands, the composition and the external events. `--config` reads another file.

```json
{"season": {"start": "2026-11-09", "weeks": 9, "lastDay": "thursday"},
 "bands": {
   "work":   {"prime": "12-15", "quiet": "1-7"},
   "casual": {"prime": "12-17", "quiet": "2-7"},
   "prime":  {"prime": "11-18", "quiet": "2-4"}},
 "composition": {
   "entrance": {"modes": ["Crosskeys", "Crosskeys 2024"]},
   "glitched": {"between": true, "dailyMax": 1, "modes": ["HMG [VT]", "No Logic Double-Rods [DR]"]},
   "fun": {"modes": []},
   "throwback": {"weeklyMax": 4, "onlyPrimeTime": true,
                 "modes": ["Influkeys", "Standard", "XD Hunt", "Crisscross"]}},
 "events": [
   {"name": "Weekly Entrance race", "weekly": "monday", "start": "01:00",
    "timezone": "UTC", "hours": 3, "effect": "avoid", "categories": ["entrance"]},
   {"name": "Tournament", "date": "2026-11-22", "start": "14:00",
    "timezone": "America/New_York", "hours": 5, "effect": "block"}]}
```

**Season.** `season` (optional) sets the period to plan. It takes these keys, and no others:
- **`start`**: the first date, `YYYY-MM-DD`. Without it, the season starts on the first date after the mirrored schedule (see *Period*).
- **`weeks`**: the season's length in weeks, the pause included: the next season starts this many weeks after `start`. A whole number of at least 1, 9 by default.
- **`lastDay`**: a weekday name, lowercase, `thursday` by default. The season races until midnight ET at the end of the last such weekday in its weeks, so that weekday is a full day of races. The days left are the pause before the next season.

So a 9-week season that starts on Monday 2026-11-09 races until Thursday 2027-01-07, pauses Friday to Sunday, and the next season starts on Monday 2027-01-11. `--start` and `--weeks` override the plan file. Anything else is an error.

**Bands.** `bands` (required) holds exactly the three groups `work`, `casual` and `prime`, each with exactly the keys `prime` and `quiet`.
- Each value is a range `"first-last"` of Eastern Time hours, 0 to 23, both ends included. When the first hour is greater than the last, the range wraps past midnight, so `"22-2"` is 22, 23, 0, 1 and 2.
- A group's prime and quiet hours may not overlap. Its other hours are normal.
- Anything else is an error.

**Composition.** The planner runs exactly the listed plan categories and modes, and picks none of its own. Each plan category takes these keys, and no others:
- **`modes`** (required) lists alttpr.racing mode names. A plan category with an empty list is left out, and the report lists it.
- **`dailyMax`**: at most this many races on any date.
- **`weeklyMax`**: at most this many races in any Monday to Sunday week.
- **`onlyPrimeTime: true`**: races in prime time only.
- **`between: true`**: a between category, whose races start only between two main races.

Both limits are whole numbers of at least 1. They are caps, never targets: a category gets its share, which may be less. `onlyPrimeTime` and `between` are `true` or `false`. If every plan category is a between category, that is an error.

**Mode names.**
- A name matches a mode of any category, ignoring case and treating square brackets and parentheses alike, so `HMG (VT)` matches `HMG [VT]`. An exact match wins.
- A name that matches no mode is an error that suggests close names. A name that matches several, such as `HMG`, is an error that lists their ids.
- A name listed twice in one plan category is an error.
- A mode's category comes from its archetype: 1 simple, 2 entrance, 3 inverted, 4 keys, 5 glitched, 6 fun, 7 mystery, 9 spoiler. Another archetype is an error, and so is a category with no slots in the history window.

**Events.** Each event has a `name`, an `effect`, a `start` (`HH:MM`), a `timezone` (an IANA name) and `hours`. It also has exactly one of `weekly` (a weekday name, lowercase) and `date` (`YYYY-MM-DD`). Only `avoid` takes `categories`, which may name categories and plan categories. An `avoid` event that lists a category also avoids every plan category with a mode from it. A weekly event is placed in its own timezone, so a UTC event keeps its UTC time across daylight saving.

| Effect | Rule during the event |
|---|---|
| `avoid` | No race of the listed categories |
| `single` | No two races less than 2 hours apart |
| `block` | No ladder race at all |

A ladder race runs for 2 hours from its start, and it is **inside** an event when the two time spans overlap.

### Definitions

**History**

- **Window.** The 365 days before the newest included race's start minus one day, counted by slot start. The last day is left out, because its rooms may not be archived yet. Included races are as defined in [README › Definitions](README.md#definitions).
- **Join.**
  1. A slot's `raceId` matches the number in an included race's `getseed.php?race=<n>`.
  2. Failing that, it matches an included race without such a number, in the slot's category, that started between 5 minutes before and 30 minutes after the slot. Each race matches once.
- **Fired slot.** It counts the race's `entrants_count`, under the race's own mode as its category.
- **Cancelled slot.** It has a `raceId` but no race. It counts 0, under the category of its mode's archetype.
- **Left out of the history:**
  - slots whose race is archived but excluded for its mode (`ladder_test`);
  - slots pulled from the schedule, which have a null `raceId` and no race.
- **Errors.** A slot whose mode is not listed is an error. So is a cancelled slot whose archetype has no category.

**Day groups**

- **Groups.** Every Eastern Time date has one group, in the history and in the period alike:
  - **work**: Monday, Tuesday and Wednesday;
  - **casual**: Thursday and Friday;
  - **prime**: Saturday, Sunday and the holidays.
- **Holidays.** These dates, and only these:
  - New Year's Day, 1 January;
  - Memorial Day, the last Monday of May;
  - Independence Day, 4 July;
  - Labor Day, the first Monday of September;
  - Thanksgiving, the fourth Thursday of November;
  - Black Friday, the day after Thanksgiving;
  - Christmas Eve, Christmas Day and New Year's Eve: 24, 25 and 31 December.

  A holiday that falls on a weekend moves no day off to a weekday.
- **What the group decides:** a date's group-hour factors, its bands and its races a day. Featured races alone go by weekday: see *Featured races*.

**Demand model**

- **Expected entrants** of a race in category $c$ and mode $m$, starting at Eastern Time hour $h$ on a date of day group $g$:

  $$E = \mathrm{block}_c(\lfloor h/4 \rfloor) \cdot \mathrm{hour}_g(h) \cdot \mathrm{mode}(m)$$

- **Shrinkage.** Every factor is a ratio of sums, shrunk toward a prior as $(n \cdot r + 20 \cdot \mathit{prior}) / (n + 20)$. Here $n$ is the factor's slots, and $r$ is its actual entrants divided by what the other factors predict for those slots. When that prediction is 0, $r$ is the prior.
- **Block factors.**
  - Let $b$ be each slot's prediction from group-hour and mode.
  - A category's rate is its actual entrants over $\sum b$.
  - A block's overall factor is its actual entrants over $\sum \mathrm{rate} \cdot b$.
  - $\mathrm{block}_c$ is shrunk toward the category's rate × the block's overall factor.
- **Fitting.** Group-hour and mode start at 1 and the blocks are fitted. Then two passes refit, in this order:
  1. **Group-hour**, prior 1. Each group's 24 factors are smoothed around the clock as $(r_{h-1} + 2r_h + r_{h+1})/4$. Then they are scaled so that, in each block, its twelve group-hours (3 groups × 4 hours) average 1. A group thus keeps its own level in every block.
  2. **Mode**, prior 1.
  3. **The blocks.**

  A group-hour with no slots is 1, and so is a mode with no slots, which therefore draws like its category's average.
- **Fill.** The fired share of a category's slots in each block. A block with no slots takes the category's overall share. It is used only to count expected cancelled rooms.

**Plan categories**

- **Sources.** The categories of a plan category's modes, in list order. A plan category with more than one source is **mixed**.
- **Expected entrants** of a plan category at a group-hour is the mean, over its modes, of $E$ with the mode's own category and factor.
- **Most popular mode.** The first of its modes when they are ranked: those with at least 20 slots in the window first, then by mode factor (highest first), then list order.
- **Main and between categories.** A plan category with `between: true` is a **between category**, and its races are **between races**. Every other plan category is a **main category**, and its races are **main races**.
- **Pair score.**
  - The pair score of two categories is their score in [Mode overlap](#mode-overlap), measured over the whole archive, together with whether it is weak evidence.
  - The pair score of two plan categories is the highest of their sources' pair scores. It is weak when any of those is. It is missing when they share a source or any of those scores is missing.

**Bands**

- **Bands.** Each group-hour is **prime**, **quiet** or **normal**, as the plan file's `bands` say. The bands hold 40, 39 and 89 weekday-hours of the week, each weekday counted in its own group.
- **Value.** Each of the 72 group-hours gets a value: the mean, over the plan categories, of their expected entrants there.
- **Fire rate.** A category's fire rate in a band is its fired window slots in that band divided by all its window slots there. A slot's band is the band of its own date's group at its hour. A band with no slots has a rate of 0.
- **Open bands.** A plan category may race in a band when every one of its sources has a fire rate of at least 10% there. With `onlyPrimeTime`, it may race only in prime time, and only if prime time is open. A plan category with no open band is an error.

**Period**

- **Period.** From 00:00 ET on the start date to 00:00 ET after the season's last race day: the last `lastDay` weekday before the start date plus `weeks` weeks. Every date is a full day, and a race belongs to the date it starts on.
  - The start is `--start`, else the plan file's `season.start`, else the first date whose 00:00 ET is at least 2 hours after the last mirrored slot. The weeks are `--weeks`, else the plan file's `season.weeks`, else 9.
  - Its days are counted as Eastern Time dates, so a daylight-saving change cannot shift the count.
  - Today's grid holds 12 races a day over the same days.
- **Start times.** A race starts on the hour, on the Eastern Time wall clock. A wall-clock hour that daylight saving skips has no start, and a repeated hour takes its first occurrence.
- **Sets.**

  | Set | work | casual | prime |
  |---|---|---|---|
  | light | 7 | 8 | 9 |
  | medium | 8 | 9 | 10 |
  | full | 9 | 10 | 11 |
  | bonanza | 10 | 11 | 12 |

  A date's **N** is the set's main races a day for the date's group. A set's races a week is the sum over a week without holidays. Between races come on top of N.

**Featured races**

- **Candidates.** For each weekday, the hours that are prime in that weekday's own group, ignoring holidays. A category has candidates only if prime time is open to it. A between category's candidates run from 01:00 to 22:00 only, so that its bracket can lie on the same date.
- **Skipped candidates.** A candidate is skipped if, in any week of the period, its race would be inside either of these:
  - a weekly `block` event;
  - a weekly `avoid` event that lists the category.
- **Score.** `(entrants in its sources' past slots at that weekday-hour + 5 · E) / (those slots + 5)`.
  - $E$ is the expected entrants of the category's most popular mode, at that weekday's group and hour. For a mixed category, it is the expected entrants of the plan category.
  - A category's best candidate on a weekday is the one with the highest score. Ties go to the earlier hour.
- **Assignment.**
  - Each plan category gets a weekday of its own, and its best candidate on that weekday. Between categories take part like the others.
  - The assignment with the highest summed score wins. Ties go to the first assignment in order, with the categories in plan order and the weekdays counted from Monday.
  - More than 7 plan categories is an error. So is having no assignment.
- **Dating.** A featured race keeps its weekday and hour all season, on holidays too.
  - It is skipped on a date where daylight saving skips its hour, where a `block` event covers it, or where an `avoid` event that lists its category covers it. Its hour is then open to other races.
  - A featured race of a between category is a between race. It is also skipped on a date whose N is below 2, and on a date with no open **bracket**: two start times on the same date, one 1 or 2 hours before the race and one 1 or 2 hours after it, each outside every `block` event.
  - Two featured races less than 2 hours apart are an error.

**Placement**

The featured races of the whole period come first. Then each date, in order, takes its main races. A date's between races follow right after the next date's main races, and the last date's at the end, so that a gap across midnight has its main races on both sides.

- **Hours.**
  - **Open hours** are the date's start times that meet all of these:
    - no race is placed there yet;
    - the time is not inside a `block` event;
    - it is at least 2 hours from every main race placed so far, on any date, including every featured main race;
    - if a race placed so far starts less than 2 hours from it, neither is inside a `single` event.
  - **Choice.** From the open hours, the date takes a set whose hours are at least 2 hours apart from each other and from the date's featured main race. The set holds at most N less the main races the date already has.
  - **Bracket.** On a date with a featured between race, the set must hold an hour 1 or 2 hours before that race and one 1 or 2 hours after it. A main race already placed there counts. If no set can, the date takes its best set without the bracket.
  - **Order.** Among the sets allowed, the date takes the one with, in order:
    1. the most hours;
    2. the most hours left for the next date of the period: how many hours it could then take by the rules of *Choice*, up to its N less its featured main races, counted with only its featured races placed;
    3. the most prime hours;
    4. the fewest **short gaps**: two consecutive main races less than 3 hours apart, unless both start in prime time. The gaps counted run from the last main race placed before the date's first one, through the date's chosen hours and its featured main race;
    5. the most normal hours;
    6. the highest total value;
    7. the earliest hours.

    A dynamic program finds it. Rule 2 keeps a date from ending so late that a 12-race date after it cannot start at midnight.
- **Fit.** A category fits a start when all of these hold:
  - its band is open to the category;
  - it is not inside an `avoid` event that lists the category;
  - the category is under its `dailyMax` on that date and its `weeklyMax` in that Monday to Sunday week;
  - the race just before it and the race just after it, in time, are of other categories. For a main category these are the main races just before and after it, so a between race in a gap does not let a category run twice in a row;
  - if a race starts less than 2 hours from it, neither is inside a `single` event.
- **Categories.** The chosen hours take main categories in band order: prime, then normal, then quiet, each in time order. Each hour takes one of the main categories that fit there, chosen by these rules in turn:
  1. not the category of the race at the same wall-clock time 7 days earlier;
  2. the lowest **share ratio**: the category's races so far in that band, featured races included, divided by its **weight** in the band;
  3. not the category of the race at the same wall-clock time 1 day earlier;
  4. plan order.

  An hour that no main category fits stays without a race. It is an **empty hour**, and the proposal's notice counts it.
- **Weight.** A category's weight in an open band is its mean expected entrants over the band's weekday-hours. In a closed band, or a band with no hours, the weight is 0, and the share ratio of a category with weight 0 is infinite.
- **Between races.**
  - **Gap hours** of a date are its start times that hold no race, are outside every `block` event, and start 1 or 2 hours after one main race and 1 or 2 hours before the next, with no between race between those two main races.
  - **Due.** A between category is due in a band when its share ratio there is at most the band's **pace**: all races so far in that band, of every category and featured races included, divided by the summed weight of all plan categories in the band.
  - **Fit.** A between category fits a gap hour by the rules of *Fit* above.
  - **Order.** The date's gap hours are tried in band order, prime then normal then quiet. Within a band they go by value, highest first, and ties go to the earlier time. Each takes one of the between categories that are due in its band and fit, chosen by the rules of *Categories*, or stays free. Due and gaps are checked again before each gap hour, so a gap takes at most one between race.
  - A between category that finds no gap hour stays due. Between races never make a date short.
- **Shortfall.** A date that ends with fewer than N main races is in the proposal's **shortfall**, and its notice counts it.
- **Modes.**
  - A featured race, and every prime-time race, of a plan category with one source runs its most popular mode.
  - Every other race takes its plan category's modes in turn, in list order and time order. So does every race of a mixed category, featured ones included, which makes a mixed category's featured mode rotate.

**Results**

- **Expected entrants** is the sum of $E$ with each race's own mode and that mode's category, less the clash.
  - The clash is counted for each two races less than 2 hours apart, as their pair score × the smaller of their two expected entrants. It is charged to the race with the fewer expected entrants, or to the later race on a tie.
  - A missing pair score counts as 1.
  - The clash charged to one race is at most its own expected entrants.
- **Expected cancelled rooms** is the sum of 1 − the fill of that category in the race's block.
- **Weekly repeats** counts the races whose category also ran at the same wall-clock time 7 days earlier, when neither race is a featured race.
- **Ranking.** The report ranks the four proposals by expected entrants per race, between races included, and marks the best one.
- **Races.** Every race of the proposal, and the between races among them.
- **Races a day.** The fewest and the most main races on any date of the proposal.
- **Short gaps.** The proposal's consecutive main races less than 3 hours apart, unless both start in prime time.
- **Unbracketed.** The between races without a main race 1 or 2 hours before them and another 1 or 2 hours after them. Only a featured between race can be one.
- **Composition.** For each plan category, the report lists:
  - its modes with their factors, the most popular mode, retired modes, and modes with no slots in the window;
  - its limits, whether it is a between category, its open bands and its weights.

  It also lists the plan categories left out.
- **Notices.** A proposal's notice counts its shortfall dates, its empty hours and its unbracketed races.
- **Today's baseline** is the newest season that ended inside the history window, scored with the same model, next to its actual entrants and cancelled rooms.
- **CSV.** `--out DIR` writes `schedule-<set>.csv`, with the columns `date,time,category,mode,mode_id,utc`:
  - `date` and `time` are Eastern Time;
  - `category` is the plan category;
  - `mode` and `mode_id` are the alttpr.racing mode, whose own category the race counts under;
  - `utc` is ISO 8601.
- **JSON.** It writes `schedule-<set>.json` as `[{"time": "<unix seconds>", "mode": <id>}]`, the shape of the schedule API's rows.
- **Report.** `--html PATH` shows, for each proposal, its full schedule: one date-by-hour grid per Monday to Sunday week of the period, with every race in its hour and the featured and between races marked.

**Success rates**

- **Rate.** For each category with slots in the window, and each of the 168 weekday-hours:
  - the window's scheduled slots, the fired ones, and the rate, fired ÷ scheduled;
  - a weekday-hour never scheduled has no rate, and a rate below 20% is **low**;
  - the weekday-hour's band is the band of the weekday's own group at that hour;
  - it is **closed** when the category's fire rate in that band is below 10%, the rule of *Open bands*.
- **Band rates.** For each category and band: the fired and scheduled slots, the fire rate, and whether the band is closed.
- **CSV.** `--out DIR` also writes `success-rates.csv`, with the columns `category,weekday,hour,band,scheduled,fired,rate,low,closed`:
  - one row per category and weekday-hour, categories by name, then Monday first, then hour;
  - `weekday` is `Mon` to `Sun` and `hour` is 0 to 23, Eastern Time;
  - `band` is `prime`, `normal` or `quiet`;
  - `rate` has 3 decimals and is blank when never scheduled;
  - `low` and `closed` are `true` or `false`.
- **Page.** `--rates PATH` writes the success-rate page:
  - one weekday-by-hour grid per category, highest overall fired share first;
  - a cell shows its rate as a whole percentage, and on hover "fired of scheduled";
  - low cells are highlighted, closed cells are marked, and cells never scheduled are blank;
  - each category also gets its band rates;
  - it covers every category with slots in the window, including those the plan leaves out. A mixed plan category has no grid of its own, because its races count under their own categories.

Thresholds are constants at the top of the module.

### Limits

- **Observational.** The turnout and fill rates are what past slots drew. They do not control for announcements, competing events or player availability. A category that past schedules put at bad hours may look weaker than it is.
- **Day groups and holidays.** Monday to Wednesday share one profile, and so do Thursday and Friday. A holiday is treated as a weekend day, but the history holds only a few holidays, so its Prime-day factors come almost entirely from weekends.
- **Bands.** They are set by hand in the plan file, not learned. The model still values each hour, and the report shows those values next to the bands, so a band that no longer fits the demand can be seen.
- **Shares by turnout.** The strongest categories take the most races, and Entrance often runs three times a day. The demand model cannot see that a category's races draw fewer racers the more of them run on one date. [Schedule strategy comparison](#schedule-strategy-comparison) corrects for that.
- **Band rule.** It pools each band over the three groups. A category that fires one time in ten is allowed. A band can be closed by bad luck in a category with few slots. As of the 2026-11-04 archive, Spoiler fires 9% of its quiet slots, just under the line.
- **Hours before categories.** A date's hours are chosen before its categories, so an hour may stay empty when no category fits it. That has not happened with the current plan file.
- **Between races.** The ladder has rarely run races an hour apart, so their clash cost comes from the Mode overlap score, which is an upper bound. Spoiler's scores are weak evidence and high, 0.83 with Entrance, so its between races score low even though the plan file treats it as a category that shares few racers. The cap keeps a race between two others from losing more than its own entrants.
- **Featured weekdays.** With more than 7 plan categories, they cannot all have a weekday of their own.
- **Cancelled rooms.** A cancelled room may have had one entrant. It counts as zero.
- **Mixed categories and old modes.** A mixed category is assumed to draw the mean of its modes, each like its own category at the same hours. A mode with no slots in the window, such as a long-retired one, is assumed to draw like its category's average.
- **Baseline.** It is scored by a model fitted on the same window, so it flatters the model. The proposals are scored out of sample.

## Schedule strategy comparison

`scripts/schedule_lab.py` breaks the [race schedule proposals](#race-schedule-proposals)' rules one at a time, in ten variants plus a combined eleventh, and compares each with the shipped rules. It is a standalone analysis: the dashboard build does not run it, and it changes nothing. It runs `scripts/schedule_planner.py` under other `Rules`, so it needs the same archive and schedule mirror.

```bash
python3 scripts/schedule_lab.py                      # the full comparison
python3 scripts/schedule_lab.py --variant no-between # one variant against shipped, no combined one
python3 scripts/schedule_lab.py --json lab.json      # also write every number
python3 scripts/schedule_lab.py --jobs 1             # no worker processes
```

### In plain words

The planner keeps a fixed set of rules, and each rule may cost some turnout. Each variant breaks one rule and plans the same season, for all four sets, with the shipped rules as the comparison. The plan file's bands, composition and events stay as they are in every run: the bands are set by hand, so no variant ranks them.

The planner is deterministic, so there are no seeds and no search budget. A variant, a set and a fold run once.

A variant is judged on proposals scored out of sample, so a rule that only overfits the history cannot win. The planner computes the open bands (the band rule) and the featured record from the history it is given, so the lab hands it one half and both are out of sample too. The demand model also cannot see that a category's races draw fewer racers the more of them run on one date, so every race is scored with a correction for that. A variant that piles races onto the strongest categories would otherwise look better than it is.

The Weekly Entrance `avoid` event is an external fact, so it stays in every variant. Every variant plans the same period as the planner.

### Variants

Each of variants 1 to 10 breaks exactly one rule. The **lever** is what the lab changes: a field of `Rules` in the planner (`SHIPPED` holds the rules this section calls shipped), or a lab lever that is not a `Rules` field. A **model-blind** variant may gain from something the demand model cannot price.

| # | Variant | Rule it breaks | Lever | Model-blind |
|---|---|---|---|---|
| 1 | `open-bands`, Every band open | A category never races in a band where its rooms fired less than 10% of the time | `band_rule` off | Yes: a category races in bands where it almost never fired, and the model extrapolates |
| 2 | `no-featured`, No featured races | Each plan category gets one race in prime time, on a weekday of its own, at the same hour every week | `featured` off | No |
| 3 | `fill-shares`, Shares by fill | A band is shared by expected turnout | `shares` is `fill`: by how often its rooms fired in the band | No |
| 4 | `equal-shares`, Equal shares | A band is shared by expected turnout | `shares` is `equal`: alike | No |
| 5 | `no-between`, No between races | Glitched and Spoiler are between categories: their races always start between two other races, never in a slot of their own, and come on top of the main races a day | `between` off: the plan file's between categories are main categories like the others | Yes: a between race's clash cost comes from Mode overlap scores, an upper bound that is weak evidence for Spoiler, so the model may overcharge the shipped rule |
| 6 | `two-hour-gaps`, Two-hour gaps | Outside prime time, 3 hours apart is preferred | `wide_gaps` off: main races are still at least 2 hours apart, but 2-hour gaps cost nothing | Yes: the model cannot see that racers join consecutive races more easily, or that a long race overlaps the next one, when races start 3 hours apart rather than 2 |
| 7 | `no-favourite`, No favourite mode | A category's prime-time races run its most popular mode | `favourite` off | No |
| 8 | `no-rotation`, No rotation | A race avoids the category that ran at the same hour a week earlier, then the one that ran there a day earlier | `rotation` off | Yes: racers do not tire of one category at a repeated hour |
| 9 | `no-plan-caps`, No plan caps | The plan file's `dailyMax`, `weeklyMax` and `onlyPrimeTime` | The caps are removed from the plan file. `between` is not a cap, so it stays, and so do the bands | No |
| 10 | `no-holidays`, No holidays | A holiday counts as a prime-group day, with that group's factors, bands and races a day | Lab lever `holidays` off: the planning model is fitted without holidays. The scoring model always counts them | No |
| 11 | `combined`, Combined | Every rule that variants 1 to 10 broke and won | The levers of the combining variants, merged | Marked when any part is |

- `two-hour-gaps` leaves main races at least 2 hours apart. Only the preference for 3-hour gaps outside prime time goes, so more short gaps appear.
- `fill-shares` and `equal-shares` both set `shares`, so they cannot be merged. The combined variant's rule is fixed under [Measures and rules](#measures-and-rules).

### Measures and rules

All of these were fixed before the comparison first ran.

- **Measures**, per set, each compared with the shipped run of the same fold:
  - **Entrants a race**: expected entrants over the season, after clash, over its races. Higher is better.
  - **Weakest category a race**: the lowest of the plan categories' expected entrants a race, before clash. A plan category with no races counts 0. Higher is better.
  - **Season entrants**: expected entrants over the season, after clash. Higher is better.
  - **Expected cancelled rooms**: the sum over the season's races of one minus the chance the room fires. Lower is better.
- **Volume guard.** Every plan category keeps at least 75% of its shipped races, counted per set, in each of the two out-of-sample folds. A variant that fails it is disqualified, and the report still shows it.
- **Verdict.** A measure's change is relative to shipped, positive in its good direction, one change per out-of-sample fold. Where the shipped measure is 0, the change counts as 0.
  - **Better**: the mean change over the two folds is at least 0.5%, and the variant is better in both folds.
  - **Worse**: the mean change is at least 0.5% in the bad direction.
  - **Same**: anything else.
- **A win.** The guard holds, no measure is worse in any of the four sets, and at least one is better.
- **The combined variant** merges the levers of these, in order of preference:
  1. every winner, when there are at least two;
  2. else every variant that is not disqualified and has no worse measure, when there are at least two;
  3. else it is not run, and the report says so.

  Two parts that change one lever differently cannot be merged, and the run stops.
- **Recommendation.**
  - A winner the model prices is recommended.
  - A model-blind winner is listed for a real trial rather than recommended, because its gain may rest on something the model cannot price.
  - A combined variant that wins is recommended, unless it holds a model-blind part, in which case it is listed for a real trial.
- **Diagnostics.** They are printed and never decide:
  - races in a band the shipped band rule closes to their category, judged by the planning half's own history;
  - category-dates with 4 or more races;
  - between races;
  - short gaps: consecutive main races less than 3 hours apart, unless both start in prime time;
  - dates with fewer main races than the day group asks for;
  - picked hours that no category could take.

### Scoring

- **Cross-fit.** The history's Monday to Sunday weeks, in Eastern Time, alternate between two halves, A and B, counted from the Monday 2000-01-03. A proposal is planned with the demand model and the history of half A and scored with the model of half B. Then the halves swap. The two folds are judged one by one, as the verdict above says. This is the score that decides.
  - The planner computes the band rates, and so the open bands, the band shares and the featured record, from the `history` it is given. The lab gives it the planning half, so both halves are out of sample for them.
  - The [Mode overlap](#mode-overlap) pair scores come from the whole archive, as the planner reads them. They price the clash of races less than 2 hours apart, the between races included, in both halves alike.
  - A half with no slots for one of the plan's categories is an error, because it can neither plan nor score that category.
  - A variant whose out-of-sample run cannot be planned has no score and cannot win.
- **Scoring model.** Each race is re-scored with the scoring half's model, which always counts holidays, times the saturation ratios below. A variant that plans without holidays is still scored with them.
- **Saturation correction.** For each half, the lab counts a category's slots on one date and compares what they drew with the model. A race is scored with the ratios for its source category's races on its date, for both entrants and fill: expected entrants times the ratio of actual to expected entrants, and the fill times the ratio of fired to expected rooms, capped at 1. Each ratio is shrunk toward 1 with 20 slots. The lab uses each half's own table.
- **Clash.** The planner's `clash` is recomputed from the corrected entrants, for any two races less than 2 hours apart, with the whole-archive pair scores. A missing score counts as 1, each pair is charged to the race with fewer entrants, and no race loses more than its own entrants. Between races are charged like any other.
- **In sample.** The whole year plans and scores itself, without saturation, and the table shows it next to the out-of-sample score. It never decides.

### How it was tested

`tests/test_schedule_lab.py` uses the shared fixtures in `tests/schedule_fixtures.py`, a synthetic world with four categories, and never touches the network. It covers these:
- each variant changes exactly the levers it names, every `Rules` field is broken by some variant, and the combined variant merges them;
- the plan caps are stripped from the plan file only when lifted, `between` and the bands stay, and the input is never changed;
- the context carries the plan file's bands, and the planner gets them as `layout`;
- the halves partition the slots by Monday to Sunday weeks in Eastern Time;
- the saturation table is shrunk per count of slots on a date;
- scoring: hand-checked numbers for entrants, clash, fill, the weakest category and crowded dates, the clash going through the planner's `clash` with its cap and its missing-score rule, and an identity table reproduces the planner's own `expected_entrants` and `expected_cancelled`;
- the closed-band diagnostic counts races in bands the shipped rule closes, and the between and short-gap diagnostics match the proposal;
- fold means keep the folds in order, and a failed fold is an error;
- verdict tables for the two-fold rule, the guard in either fold, and a win that fails when one measure is worse in any of the four sets;
- the combined rule;
- each lever reaches the planner and changes something on the fixture world, and the band rule does so on a history where a band is closed;
- no-holidays plans with a holiday-free model and scores with holidays;
- a smoke run of every variant, which is also run twice to check it is deterministic.

### Result

On the archive as of 2026-11-04, run once with the rules above:
- the history runs from 2025-11-03 to 2026-11-03, with 4,192 slots in halves of 2,107 and 2,085;
- the season runs from 2026-11-07 to 2027-01-07;
- each variant, set and fold ran once, and the whole comparison took about 20 seconds.

These figures are a frozen snapshot. The races are the mean of the two out-of-sample folds. Each measure shows its mean change from shipped and its verdict: + better, = same, - worse. For cancelled rooms, a fall is better.

```text
Light            races    a race            weakest             season          cancelled           guard win
shipped            518      5.95               0.71              3,080                128
open-bands         522      5.91   -0.7%-      0.67   -5.0%-     3,080   +0.0%=       131   +2.5%-  ok    no
no-featured        512      6.27   +5.7%=      0.71   -0.1%=     3,216   +4.6%=       123   -4.3%=  short no
fill-shares        546      5.26  -11.4%-      0.70   -0.6%-     2,870   -6.7%-       157  +22.0%-  ok    no
equal-shares       636      4.25  -28.5%-      0.71   +0.8%+     2,701  -12.3%-       236  +84.7%-  short no
no-between         485      6.20   +4.4%=      0.68   -3.7%-     3,005   -2.3%-       125   -3.0%=  ok    no
two-hour-gaps      522      6.56  +10.4%+      0.71   +1.5%=     3,430  +11.4%+       113  -12.1%+  ok    no
no-favourite       518      5.77   -3.0%-      0.71   +0.0%=     2,987   -3.0%-       128   -0.0%=  ok    no
no-rotation        518      5.90   -0.6%-      0.71   +0.0%=     3,058   -0.6%-       133   +3.2%-  ok    no
no-plan-caps       516      5.91   -0.7%-      0.71   +0.1%=     3,047   -1.1%-       134   +4.5%-  short no
no-holidays        512      5.94   -0.1%=      0.71   +0.6%=     3,043   -1.2%-       127   -0.9%+  ok    no

Medium           races    a race            weakest             season          cancelled           guard win
shipped            581      5.54               0.70              3,217                160
open-bands         586      5.49   -0.9%-      0.66   -6.6%-     3,217   +0.0%=       165   +3.4%-  ok    no
no-featured        576      5.96   +7.7%+      0.69   -1.7%-     3,432   +6.6%+       146   -8.6%+  ok    no
fill-shares        611      5.08   -8.2%-      0.70   -0.3%=     3,104   -3.5%-       181  +14.2%-  ok    no
equal-shares       703      4.11  -25.7%-      0.71   +1.2%+     2,892  -10.1%-       270  +72.0%-  short no
no-between         546      5.82   +5.1%+      0.67   -3.2%-     3,179   -1.2%-       150   -6.5%+  ok    no
two-hour-gaps      588      6.00   +8.4%+      0.70   -0.6%-     3,527   +9.7%+       148   -7.6%+  ok    no
no-favourite       581      5.37   -3.0%-      0.70   +0.0%=     3,118   -3.0%-       160   -0.0%=  ok    no
no-rotation        581      5.55   +0.2%=      0.70   +0.0%=     3,225   +0.2%=       163   +1.1%-  ok    no
no-plan-caps       579      5.54   +0.1%=      0.70   -0.3%=     3,208   -0.2%=       161   +1.1%-  short no
no-holidays        575      5.53   -0.2%=      0.70   +0.8%+     3,178   -1.2%-       157   -1.9%+  ok    no

Full             races    a race            weakest             season          cancelled           guard win
shipped            646      5.25               0.70              3,394                191
open-bands         652      5.21   -0.8%-      0.65   -6.8%-     3,394   +0.0%=       196   +2.9%-  ok    no
no-featured        640      5.65   +7.4%+      0.72   +2.2%+     3,620   +6.5%+       177   -7.4%+  ok    no
fill-shares        680      4.81   -8.4%-      0.70   -0.2%=     3,274   -3.5%-       216  +15.0%-  ok    no
equal-shares       776      4.02  -23.3%-      0.71   +1.6%=     3,121   -7.9%-       302  +62.7%-  short no
no-between         607      5.56   +5.8%+      0.61  -12.7%-     3,376   -0.6%-       175   -8.4%+  ok    no
two-hour-gaps      650      5.51   +4.9%+      0.70   -0.6%-     3,578   +5.5%+       189   -1.2%=  ok    no
no-favourite       646      5.11   -2.7%-      0.70   +0.0%=     3,301   -2.7%-       191   -0.0%=  ok    no
no-rotation        646      5.19   -1.4%-      0.70   -0.6%-     3,350   -1.4%-       199   +3.7%-  ok    no
no-plan-caps       643      5.25   -0.0%=      0.69   -1.4%-     3,376   -0.5%=       191   +0.5%-  short no
no-holidays        640      5.30   +0.9%+      0.70   -0.3%=     3,391   -0.1%=       188   -1.5%+  ok    no

Bonanza          races    a race            weakest             season          cancelled           guard win
shipped            702      5.06               0.69              3,547                222
open-bands         708      5.01   -0.8%-      0.65   -6.5%-     3,547   +0.0%=       227   +2.7%-  ok    no
no-featured        705      5.40   +6.6%+      0.70   +1.6%=     3,808   +7.1%+       212   -4.4%+  ok    no
fill-shares        739      4.67   -7.5%-      0.69   -0.4%=     3,451   -2.6%-       247  +13.4%-  ok    no
equal-shares       837      3.84  -23.9%-      0.71   +2.3%+     3,211   -9.2%-       341  +59.2%-  short no
no-between         662      5.35   +5.8%+      0.57  -16.5%-     3,546   -0.1%=       205   -7.2%+  ok    no
two-hour-gaps      702      5.11   +1.1%+      0.69   +0.0%=     3,585   +1.1%+       220   -0.7%=  ok    no
no-favourite       702      4.93   -2.4%-      0.69   +0.0%=     3,462   -2.4%-       222   -0.0%=  ok    no
no-rotation        702      5.15   +1.8%+      0.69   +0.1%=     3,612   +1.8%+       218   -2.4%+  ok    no
no-plan-caps       697      5.10   +0.9%+      0.67   -2.9%-     3,555   +0.3%=       219   -0.7%=  short no
no-holidays        696      5.10   +0.9%=      0.69   +0.1%=     3,545   +0.0%=       217   -1.9%+  ok    no
```

The weakest category is glitched in every row.

- **No variant wins, so the shipped rules stand.** The combined variant was not run, because fewer than two variants qualified.
- **The 3-hour preference costs the most.**
  - With 2-hour gaps costing nothing, entrants a race rise by 10.4% in Light, 8.4% in Medium, 4.9% in Full and 1.1% in Bonanza. Season entrants rise by about as much, and in Light and Medium fewer rooms are cancelled.
  - It fails only on the weakest category, which draws 0.6% less in Medium and Full.
  - Light and Medium pay the most, because their races can mostly sit 3 hours apart, so the preference moves races out of normal hours into quiet ones. In the whole-year plans, Light runs 70 main races in quiet hours with the preference and none without it, and Medium 98 and none. Short gaps rise from 12 to 258 a season in Light without it.
  - It is model-blind: the model cannot see what 3-hour gaps give racers. Even a win would only be listed for a real trial.
- **Between races cost entrants a race but keep the season and the weakest category.**
  - With glitched and spoiler as main categories, entrants a race rise by 4.4% to 5.8%, because the between races are extra races that draw about one racer each.
  - But season entrants fall by up to 2.3%, and the weakest category draws 3.2% to 16.5% less a race.
- **Featured races cost more than before.**
  - Without them, entrants a race rise by 5.7% to 7.7%. Under the previous rules the gain was 1.7% to 5.0%.
  - A featured glitched or spoiler race needs a main race on each side, and the two must be of different categories. So Entrance runs fewer races: 155 a season in Light instead of 180 without featured races.
  - Without featured races, glitched falls below the volume guard in Light, and the weakest category draws 1.7% less in Medium.
- **Shares by turnout still carry the most weight among the old rules.** Shares by fill lose 7.5% to 11.4% a race. Equal shares lose 23% to 29%, cancel 59% to 85% more rooms and fail the guard in every set.
- **The favourite mode in prime time pays.** Without it, entrants a race fall 2.4% to 3.0% in every set.
- **The band rule protects the weakest category.** Opening every band lets glitched and spoiler race in quiet hours, 4 to 6 races a season. Entrants a race fall 0.7% to 0.9%, and the weakest category 5% to 7%.
- **The plan caps cost at most 1% a race.** Without them, throwback runs 85 to 116 races a season instead of 31, and keys or mystery fall below the volume guard.
- **No rotation** and **no holidays** stay within 2% a race either way. No rotation is model-blind anyway.
- **Bonanza is short on some dates out of sample.** Planned with one half of the history, its featured hours can leave a 12-race date one race short: 9 dates a season. The whole-year plan has none.
- **Crowding.** Under the shipped rules, one category runs 4 or more races on a date 20 times a season in Light and 83 times in Bonanza. These are mostly Entrance and Simple. The saturation correction prices them with its 4-or-more ratio, which rests on few slots (see *Limits*).
- **Against the rules before this change**, scored the same way except that the clash had no cap:

  | Set | Before: races, a race, season | Now: races, a race, season | Season change |
  |---|---|---|---|
  | Light | 485, 6.89, 3,341 | 518, 5.95, 3,080 | −7.8% |
  | Medium | 546, 6.41, 3,501 | 581, 5.54, 3,217 | −8.1% |
  | Full | 607, 5.89, 3,576 | 646, 5.25, 3,394 | −5.1% |
  | Bonanza | 666, 5.37, 3,582 | 702, 5.06, 3,547 | −1.0% |

  - Now each set runs more races, because the between races come on top, but draws fewer entrants over the season.
  - The 3-hour preference accounts for most of the drop: without it, Light would reach 3,430 and Medium 3,527, above the previous rules.
  - The hand-set bands, the between races and the featured between races account for the rest.
  - The model cannot see what the owner's rules give racers, so this is the price the model puts on them, not a verdict.

### Limits

- **Observational.** The turnout and fill rates are what past slots drew. They do not control for announcements, competing events or player availability.
- **Saturation.** It is measured from history, where a category rarely had more than 3 races a date. The 4 or more bucket rests on few slots.
- **Model-blind variants.** Open bands extrapolate to bands where a category almost never fired. No rotation assumes racers do not tire of a repeated hour. Two-hour gaps assume racers join races as easily 2 hours apart as 3, and that a long race does not overlap the next. No between races leans on a clash cost that is an upper bound and, for Spoiler, weak evidence. Their gains need a real trial.
- **Noisier halves.** Each half holds half the history, so its model, its band rates and its featured record are noisier than the whole year's. That is the point of scoring out of sample, but it costs precision.
- **Determinism.** The planner is deterministic, so each variant, set and fold runs once. There is no spread across seeds to show how far a verdict near the tolerance could move, and only two folds back each verdict.
- **Judgement calls.** The 75% guard and the 0.5% tolerance were chosen, not derived.
- **Worker processes.** The runner uses worker processes, which need POSIX semaphores. Inside a sandbox that denies them, use `--jobs 1`.

## Rating comparison

`scripts/rating_lab.py` compares other rating algorithms with the [ladder rating](README.md#ladder-rating), on the [backtest](README.md#backtest)'s pairs and under the ladder's promises. It is a standalone analysis: the dashboard build does not run it, and it changes nothing. Its recommendation, the retune below, was adopted on 2026-11-01. Throughout this section, **shipped** means the rating it was compared with ($\beta = 12$, $\sigma_0 = 8$, floor 4, no damping), which the lab keeps as its baseline so the comparison stays reproducible.

```bash
python3 scripts/rating_lab.py --tune               # each candidate's setting, from races before 2026 (several minutes)
python3 scripts/rating_lab.py --tune --family elo  # one candidate only
python3 scripts/rating_lab.py                      # re-derive CHOSEN from --tune, then the comparison
```

### Candidates

Every candidate reads placement only and is scored on exactly the pairs the backtest scores.

| Candidate | What it is |
|---|---|
| Bradley-Terry full pair, retuned | The ladder rating with every parameter re-picked, plus field-size damping: a race's update is scaled by $(n-1)^{-\alpha}$ for $n$ entrants, so a 32-racer race no longer counts as 31 independent matches |
| Thurstone-Mosteller full pair | Weng and Lin's Algorithm 3: the same pairs with a normal win chance |
| Bradley-Terry partial pair | Algorithm 2: each place meets only the next place, and the last place meets every non-finisher |
| Plackett-Luce | Algorithm 4: the whole finishing order at once, non-finishers tied last. Racers who share a place, and non-finishers, never count against each other |
| Elo, multi-player | Every decided pair is an Elo game, with a K that falls over a racer's first races |

Left out because they cannot keep the ladder's promises:
- Glicko-2: ratings decay while a racer is away.
- Whole-History Rating, TrueSkill Through Time and batch refits: ratings move when others race.
- Additive drift: a winner can lose points.
- Mode ratings seeded from the overall rating.

TrueSkill is left out because Thurstone-Mosteller is its close approximation.

### Rules

All of these were fixed before any 2026 race was scored for a candidate.

- **Promises.** A candidate is eligible only if, over the whole archive, overall and in every mode, it keeps all of these:
  - no winner's points fall;
  - every debut shows 1000;
  - no state changes while a racer is away;
  - finish times change nothing.

  Mode ratings use the overall parameters.
- **Tuning** reads races before 2026-01-01 only, and `tune` cuts there itself.
  - It scores July to September and October to December 2025. The earlier months are warm-up, because everyone in them is new.
  - A setting must move a veteran's points at least as much as the shipped rating does over those months, which is a median of 8. This keeps the livelier ladder.
  - Among the lively settings within one standard error of the best, the simplest wins.
  - A choice on a grid edge extends that axis and tunes again.
  - Points are scaled per family, so that 400 points are about 10:1 odds under that family's own win chance.
- **The comparison runs once.**
  - The primary measure is 2026 pair log loss, paired against the shipped rating.
  - The interval is the wider of a race bootstrap and an Eastern Time ISO-week bootstrap: 2,000 resamples, fixed seed.
  - The p-value is the larger of the two, Holm-adjusted across the candidates.
- **A change** needs all of these:
  - eligibility;
  - Holm significance;
  - at least 0.003 better. That is about twice the 0.0017 that floor 3 gains over floor 4 over September to December 2025, the accuracy already traded for liveliness;
  - better in both halves of 2026;
  - no worse when each race counts once.

  A new model must also beat the retuned ladder rating the same way; otherwise retuning is the simpler change.

### How the candidates were tested

- **Golden values.**
  - The OpenSkill reference package, version 6.2.0, run once outside the repository, supplied exact results for Algorithms 2 to 4 on fixed races.
  - Its configuration was first proved by reproducing `ladder_rating`'s own update to 1e-14.
  - The candidates match those results to 1e-13.
- **Fuzz tests** check the promises on thousands of random races and states, including shared wins, which the archive has never had.
- **A known-truth simulation** checks that every candidate recovers a true skill order from simulated races.
- **Harness tests** cover all of these:
  - a coin flip scores ln 2;
  - the shipped setting reproduces the backtest;
  - tuning gives the same answer whatever 2026 holds;
  - the promise checker catches each broken promise.
- **An independent review** before the run found that Thurstone-Mosteller's points were scaled for a logistic win chance, so that 400 points meant 92:1. That held it to a stricter liveliness bar. It was fixed and re-tuned before 2026 was scored.

### Result

On the archive as of the lab run on 2026-11-01: 47,576 pairs in 1,979 races from 2026-01-01. These figures are a frozen snapshot; the README's [Backtest](README.md#backtest) runs on the current archive, so its pair counts are higher. Δ is the change in log loss against shipped, below 0 is better, with its 95% interval.

```text
                       setting                          accuracy  log loss  Δ (95% CI)                  per race
ladder rating, shipped β12 σ₀8 floor 4                     0.723    0.5414                                0.5380
BT full pair, retuned  β8 σ₀12 floor 4 α½                  0.728    0.5341  -0.0074 (-0.0091, -0.0056)    0.5314
TM full pair           β12 σ₀8 floor 3 α½                  0.728    0.5347  -0.0068 (-0.0088, -0.0048)    0.5324
Elo, multi-player      K 128→24 over 10 races, α½          0.727    0.5368  -0.0046 (-0.0060, -0.0033)    0.5340
Plackett-Luce          β6 σ₀8 floor 5                      0.716    0.5854  +0.0440 (+0.0361, +0.0517)    0.5761
BT partial pair        β5 σ₀40 floor 4                     0.693    0.5875  +0.0461 (+0.0396, +0.0530)    0.5758
racetime.gg score                                          0.695    0.5871  best-fit logistic, in-sample
```

- **Recommendation: retune the ladder rating to β 8, σ₀ 12, floor 4, with field-size damping α ½.** Adopted on 2026-11-01. The production backtest reproduced the retuned line exactly on that archive.
  - It is better in both halves of 2026: −0.0059 and −0.0090.
  - It is better in every experience bucket and every field size.
  - It is better in every mode with more than 300 pairs, except mystery, where it ties.
  - The own-mode ratings improve too, from 0.5532 to 0.5465.
- **Every candidate kept every promise.**
- **No other family is worth switching to.**
  - Thurstone-Mosteller ties the retuned rating: +0.0006 against it, interval (−0.0000, +0.0013).
  - Elo trails the retuned rating by 0.0028.
  - Plackett-Luce and partial pair do much worse. Partial pair rates only neighbouring places, so it ignores most of a race's pairs. Plackett-Luce's win chances are poorly calibrated: an expected calibration error of 0.070, against 0.020 for shipped.
- **Retuning changes how points feel**, so weigh this before adopting it:
  - A veteran's median move per race falls from 9 to 8 points over the whole archive.
  - Their 90th-percentile move falls from 33 to 20, because damping shrinks the swings of big races.
  - A veteran's DNF costs a median of 12 points instead of 14, and finishing last costs 9 instead of 6.
  - The final standings stay close: Kendall's τ is 0.93 against shipped, and 9 of the top 10 are shared.

### Limits

- The shipped rating's 2026 score was public before this work; the candidates' 2026 scores were not.
- **Grid extensions.**
  - The first tuning run put every family on a grid edge, so the edge rule extended β down to 4, σ₀ to the range 4 to 14, the floor up to 8, and Elo's K values. All of this happened before 2026 was scored.
  - Partial pair was extended three times. It stopped when the best ten settings were within 0.0001 of each other, although σ₀ = 40 still sits on the edge.
- The archive has no tied places, so the tie rules are tested only on synthetic races.
