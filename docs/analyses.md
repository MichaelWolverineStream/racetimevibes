# Standalone analyses

- **Purpose:** the four standalone analyses, with their commands, rules and limits: mode overlap, race schedule proposals, the schedule strategy comparison and the rating comparison.
- **Read before:** changing `mode_overlap.py`, `schedule_planner.py`, `schedule_lab.py`, `rating_lab.py`, `schedule-plan.json` or `tests/analyses/`.
- **Code:** those four scripts in `scripts/`; their tests are in `tests/analyses/` (see [Tests](operations.md#tests)).

Four scripts analyse the archive on their own. The build never runs them, they change nothing in `site/`, and each writes files only when an option asks it to. Each takes `--archive` to read another archive. Their HTML reports are self-contained pages, with no scripts or external requests, in the claude.ai Artifact page format, which leaves the document skeleton to the publisher.

## Mode overlap

`scripts/mode_overlap.py` measures how much the modes share racers, to find modes that could run in the same slot without making racers pick one.

```bash
python3 scripts/mode_overlap.py                  # ranked pair table
python3 scripts/mode_overlap.py --min-entries 2  # count only racers with 2+ entries in the other mode
python3 scripts/mode_overlap.py --html report.html
```

### In plain words

For a pair of modes A and B, look only at the months when both were on the schedule, and ask: what share of A's entries came from racers who also raced B that month? If A ran against B, that share of A's field would have to choose. The pair's score is the larger of the two directions, because a pairing is only as good as its effect on the mode that loses more.

### Definitions

- Races are the dashboard's included races (see [Races](definitions.md#races)), and entrants are identified by stable user ID; anonymous entries are skipped. Any entry counts, DNFs and DQs included, because entering is what a clash takes away.
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

`scripts/schedule_planner.py` proposes one season of ladder races as four sets, *Light*, *Medium*, *Full* and *Bonanza*, from 55 to 76 races a week plus the races that start between them. All four run exactly the categories and modes that the plan file lists. It learns from how many racers came to every slot the alttpr.racing schedule ever held, including the cancelled ones. The rules this section describes are `SHIPPED` in the code, and [Schedule strategy comparison](#schedule-strategy-comparison) tests deviations from them.

It reads the race archive and the schedule mirror (see [Ladder schedule mirror](archive.md#ladder-schedule-mirror)), so refresh both first. `--out` writes each proposal as CSV and as JSON, and the success rates as CSV. `--html` writes a report page and `--rates` a success-rate page. The planner is deterministic: the same archive, plan file, start date and seed always give the same proposals. `--seed N` (1 by default) sets the seed of the draw of hours, and another seed draws another season.

```bash
python3 scripts/schedule_archive.py                     # refresh the schedule mirror (network)
python3 scripts/schedule_planner.py                     # the four proposals
python3 scripts/schedule_planner.py --out proposals     # also write CSV and JSON per proposal
python3 scripts/schedule_planner.py --rates rates.html  # also write the success-rate page
python3 scripts/schedule_planner.py --start 2027-01-11 --weeks 9 --html report.html
python3 scripts/schedule_planner.py --config other-plan.json
python3 scripts/schedule_planner.py --seed 2            # another draw of the hours
```

### Terms

This section uses alttpr.racing's words, which differ from the dashboard's:

- A **category** is an alttpr.racing *archetype*. The dashboard calls it a mode: entrance, simple, keys and so on.
- A **mode** is an alttpr.racing *mode*. The dashboard calls it a preset: Crosskeys 2024, AD Keysanity and so on.
- A **plan category** is a key of the plan file's composition. Most plan categories are a category under its own name, but one may mix modes from several, as `throwback` does. The categories of its modes are its **sources**, and the races count under those categories on racetime.gg and in the dashboard.

### In plain words

A slot that never became an archived race was cancelled, because rooms with fewer than two entrants are never archived, so it counts as zero entrants. A race's expected turnout is its category's average in that four-hour part of the day, adjusted for the exact hour of the date's day group and for the mode.

The days fall into three [day groups](#day-groups): **Work** (Monday to Wednesday), **Casual** (Thursday and Friday) and **Prime** (weekends and the common US days off). The plan file splits each group's hours into **prime time**, **quiet** hours and **normal** hours (Eastern Time, both ends included). The shipped plan file sets them as:

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

Today's grid has 12 races a day, 84 a week. The planner fills the season in five steps:

1. **Featured races.** Each plan category gets one race in prime time, on a weekday of its own, alternating week by week between two adjacent prime hours. The pair of hours is picked from the category's own record at them, never inside a weekly event that rules the category out. A featured Glitched or Spoiler race is a between race like any other.
2. **Hours.** Main races start at least 2 hours apart. Each date takes its main races at the hours that hold the most prime races, then the most hours that a category marked `everyHour` still lacks, so that such a category races at every hour of the season. Outside prime time, 3 hours apart is preferred: a race goes to a quiet hour before a normal-hour gap shrinks to 2 hours. After that come the most normal races. Among the sets that are equally good by every rule, the date draws one, with odds by expected turnout. A date also looks one date ahead: it ranks its hours together with the next date's best hours after them, so a race at 23:00 pays for the 00:00 it takes from the next date and the short gap it makes at 01:00. So the hours shift from day to day, and quiet races do not always start at the same hour.
3. **Categories.** An every-hour category that has no race yet at the hour goes first. Hours that an every-hour category lacks are given out first, then prime-time races, then normal, then quiet. Otherwise each race goes to the main category furthest below its share of that band. A band is shared by expected turnout. A race avoids the category that ran at the same hour a week earlier, then the one that ran there a day earlier.
4. **Between races.** A between category races whenever it is behind its own turnout share of a band, in a gap between two main races: 1 or 2 hours after one and 1 or 2 hours before the next. A gap holds at most one between race.
5. **Alternate weeks.** In every second week, races may start an hour earlier or later, so that the two-hour sections the week before left empty get main races too. Every rule above still holds. A prime-time race stays in prime time, and each every-hour category's first race at an hour never moves. Featured races never move: they already alternate between their two hours.

**A category never races where it almost never fired.** If a category's rooms fired less than 10% of the time in a band, it never races in that band. A category with `onlyPrimeTime` races only in prime time. A category's prime-time races run its most popular mode, and its other races take its modes in turn.

The report ranks the four proposals by expected entrants per race. A separate **success-rate page** shows how often each category's rooms fired at each weekday and hour, and in each band. It highlights the rates below 20% and marks the bands closed to a category.

### The plan file

[schedule-plan.json](../schedule-plan.json) holds the season, the bands, the composition and the external events. `--config` reads another file.

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
- **`everyHour: true`**: the category gets at least one race at every one of its **target hours** over the season: the Eastern Time hours 0–23 whose band is open to it in at least one day group. With `onlyPrimeTime`, those are prime hours only. The shipped file sets it on entrance, keys and simple.

Both limits are whole numbers of at least 1. They are caps, never targets: a category gets its share, which may be less. `onlyPrimeTime`, `between` and `everyHour` are `true` or `false`, and `everyHour` is `false` by default. If every plan category is a between category, that is an error. So is a category that is both `between: true` and `everyHour: true`: a between category cannot be everyHour.

**Mode names.**

- A name matches a mode of any category, ignoring case and treating square brackets and parentheses alike, so `HMG (VT)` matches `HMG [VT]`. An exact match wins.
- A name that matches no mode is an error that suggests close names. A name that matches several, such as `HMG`, is an error that lists their ids.
- A name listed twice in one plan category is an error.
- A mode's category comes from its archetype, mapped as under [Schedule](definitions.md#schedule). An archetype without a category is an error, and so is a category with no slots in the history window.

**Events.** Each event has a `name`, an `effect`, a `start` (`HH:MM`), a `timezone` (an IANA name) and `hours`. It also has exactly one of `weekly` (a weekday name, lowercase) and `date` (`YYYY-MM-DD`). Only `avoid` takes `categories`, which may name categories and plan categories. An `avoid` event that lists a category also avoids every plan category with a mode from it. A weekly event is placed in its own timezone, so a UTC event keeps its UTC time across daylight saving.

| Effect | Rule during the event |
|---|---|
| `avoid` | No race of the listed categories |
| `single` | No two races less than 2 hours apart |
| `block` | No ladder race at all |

A ladder race runs for 2 hours from its start, and it is **inside** an event when the two time spans overlap.

### Definitions

#### History

- **Window.** The 365 days before the newest included race's start minus one day, counted by slot start. The last day is left out, because its rooms may not be archived yet. Included races are as defined under [Races](definitions.md#races).
- **Join.** Slots join included races by the rules under [Schedule](definitions.md#schedule), the same join the dashboard build uses. The planner reads a slot's race from it, and its category is the race's mode.
- **Fired slot.** It counts the race's `entrants_count`, under the race's own mode as its category.
- **Cancelled slot.** It has a `raceId` but no race. It counts 0, under the category of its mode's archetype.
- **Left out of the history:**
  - slots whose race is archived but excluded for its mode (`ladder_test`);
  - slots pulled from the schedule, which have a null `raceId` and no race.
- **Errors.** A slot whose mode is not listed is an error. So is a cancelled slot whose archetype has no category.

#### Day groups

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

#### Demand model

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

#### Plan categories

- **Sources.** The categories of a plan category's modes, in list order. A plan category with more than one source is **mixed**.
- **Expected entrants** of a plan category at a group-hour is the mean, over its modes, of $E$ with the mode's own category and factor.
- **Most popular mode.** The first of its modes when they are ranked: those with at least 20 slots in the window first, then by mode factor (highest first), then list order.
- **Main and between categories.** A plan category with `between: true` is a **between category**, and its races are **between races**. Every other plan category is a **main category**, and its races are **main races**.
- **Pair score.**
  - The pair score of two categories is their score in [Mode overlap](#mode-overlap), measured over the whole archive, together with whether it is weak evidence.
  - The pair score of two plan categories is the highest of their sources' pair scores. It is weak when any of those is. It is missing when they share a source or any of those scores is missing.

#### Bands

- **Bands.** Each group-hour is **prime**, **quiet** or **normal**, as the plan file's `bands` say.
- **Value.** Each of the 72 group-hours gets a value: the mean, over the plan categories, of their expected entrants there.
- **Draw key.** Every start gets one: $\ln(\mathit{value}) + G$, where value is its group-hour's value.
  - $G = -\ln(-\ln u)$, with $u$ = (the first 8 bytes of the SHA-256 of the text `"{seed}:{start's UTC time in ISO 8601}"`, read as a big-endian integer, + 0.5) / $2^{64}$.
  - A value of 0 or less gives a key of $-\infty$.
  - Among tied hours, the highest key is a draw with odds proportional to value (the Gumbel-max trick). For several hours, it favours dearer sets the same way.
  - The seed is `--seed`, 1 by default. `analyse(seed=...)` and `plan_proposal(seed=...)` take it.
- **Fire rate.** A category's fire rate in a band is its fired window slots in that band divided by all its window slots there. A slot's band is the band of its own date's group at its hour. A band with no slots has a rate of 0.
- **Open bands.** A plan category may race in a band when every one of its sources has a fire rate of at least 10% there. With `onlyPrimeTime`, it may race only in prime time, and only if prime time is open. A plan category with no open band is an error.

#### Period

- **Period.** From 00:00 ET on the start date to 00:00 ET after the season's last race day: the last `lastDay` weekday before the start date plus `weeks` weeks. Every date is a full day, and a race belongs to the date it starts on.
  - The start is `--start`, else the plan file's `season.start`, else the first date whose 00:00 ET is at least 2 hours after the last mirrored slot. The weeks are `--weeks`, else the plan file's `season.weeks`, else 9.
  - Its days are counted as Eastern Time dates, so a daylight-saving change cannot shift the count.
- **Start times.** A race starts on the hour, on the Eastern Time wall clock. A wall-clock hour that daylight saving skips has no start, and a repeated hour takes its first occurrence.
- **Sets.** A date's **N** is the set's main races a day for the date's group, as in the sets table under *In plain words*. A set's races a week is the sum over a week without holidays. Between races come on top of N.

#### Featured races

- **Candidates.** For each weekday, the hours that are prime in that weekday's own group, ignoring holidays. A category has candidates only if prime time is open to it. A between category's candidates run from 01:00 to 22:00 only, so that its bracket can lie on the same date.
- **Skipped candidates.** A candidate is skipped if, in any week of the period, its race would be inside either of these:
  - a weekly `block` event;
  - a weekly `avoid` event that lists the category.
- **Pairs.** A pair is two candidate hours h and h + 1 of the same date, so 23:00 never pairs with 00:00. A weekday with no pair has no candidate.
- **Score.** The score of an hour is `(entrants in its sources' past slots at that weekday-hour + 5 · E) / (those slots + 5)`. A pair scores the mean of its two hours' scores.
  - $E$ is the expected entrants of the category's most popular mode, at that weekday's group and hour. For a mixed category, it is the expected entrants of the plan category.
  - A category's best pair on a weekday is the one with the highest score. Ties go to the earlier pair.
  - Within the pair, the hour with the higher score runs in odd weeks, the earlier one on a tie. The other hour runs in even weeks. Weeks are the period's Monday to Sunday weeks counted from 1, the first maybe partial, as under *Alternate weeks*.
- **Assignment.**
  - Each plan category gets a weekday of its own, and its best pair on that weekday. Between categories take part like the others.
  - The assignment with the highest summed pair score wins. Ties go to the first assignment in order, with the categories in plan order and the weekdays counted from Monday.
  - More than 7 plan categories is an error. So is having no assignment.
- **Dating.** A featured race keeps its weekday all season, on holidays too. In odd weeks it takes the pair's first hour, in even weeks the other.
  - It is skipped on a date where daylight saving skips that week's hour, where a `block` event covers it, or where an `avoid` event that lists its category covers it. There is no fallback to the other hour. Its hour is then open to other races.
  - A featured race of a between category is a between race. It is also skipped on a date whose N is below 2, and on a date with no open **bracket** at that week's hour: two start times on the same date, one 1 or 2 hours before the race and one 1 or 2 hours after it, each outside every `block` event.
  - Two featured races less than 2 hours apart are an error.

#### Placement

The featured races of the whole period come first. Then each date, in order, takes its main races. A date's between races follow right after the next date's main races, and the last date's at the end, so that a gap across midnight has its main races on both sides. Last, the races of alternate weeks may move an hour (see *Alternate weeks*).

- **Hours.**
  - **Open hours** are the date's start times that meet all of these:
    - no race is placed there yet;
    - the time is not inside a `block` event;
    - it is at least 2 hours from every main race placed so far, on any date, including every featured main race;
    - if a race placed so far starts less than 2 hours from it, neither is inside a `single` event.
  - **Choice.** From the open hours, the date takes a set whose hours are at least 2 hours apart from each other and from the date's featured main race. The set holds at most N less the main races the date already has.
  - **Bracket.** On a date with a featured between race, the set must hold an hour 1 or 2 hours before that race and one 1 or 2 hours after it. A main race already placed there counts. If no set can, the date takes its best set without the bracket.
  - **Next date's best choice.** For a set, the set the next date of the period would then take by the rules of *Choice* and of *Order* below, except rule 2: from its open hours at least 2 hours after the set's last race, up to its N less its main races already placed, counted with only the races placed so far. That last race starts the next date's first gap. The last date of the period has none.
  - **Order.** Among the sets allowed, the date takes the one with, in order:
    1. the most hours;
    2. the most hours left for the next date of the period: the hours of the next date's best choice;
    3. the most prime hours;
    4. the most **coverage**: for each chosen hour, the number of every-hour categories, open in its band, that have no race yet at that Eastern Time hour on any date of the period (featured races count), summed over the chosen hours;
    5. the fewest **short gaps**: two consecutive main races less than 3 hours apart, unless both start in prime time. The gaps counted run from the last main race placed before the date's first one, through the date's chosen hours and its featured main race;
    6. the most normal hours;
    7. the highest total **draw key**;
    8. the earliest hours.

    Rules 3 to 7 count the set's hours together with the next date's best choice after it. A dynamic program finds it. Rule 2 keeps a date from ending so late that a 12-race date after it cannot start at midnight. Counting the next date's best choice in rules 3 to 7 makes a late race pay for what it costs the next date: a race at 23:00 takes the next date's 00:00 and makes its 01:00 a short gap, and a date leaves the next date an hour that a category still lacks. With `Rules.draw` off (lab variant `no-draw`), rule 7 is the highest total value instead.
- **Fit.** A category fits a start when all of these hold:
  - its band is open to the category;
  - it is not inside an `avoid` event that lists the category;
  - the category is under its `dailyMax` on that date and its `weeklyMax` in that Monday to Sunday week;
  - the race just before it and the race just after it, in time, are of other categories. For a main category these are the main races just before and after it, so a between race in a gap does not let a category run twice in a row;
  - if a race starts less than 2 hours from it, neither is inside a `single` event.
- **Categories.** The chosen hours that an every-hour category lacks take their categories first, in time order. Then the other chosen hours follow in band order: prime, then normal, then quiet, each in time order. So no other hour takes the category next to a lacking hour first, which would stop it running there, as no category races twice in a row. Each hour takes one of the main categories that fit there, chosen by these rules in turn:
  1. an every-hour category with no race yet at the start's Eastern Time hour, on any date so far;
  2. not the category of the race at the same wall-clock time 7 days earlier;
  3. the lowest **share ratio**: the category's races so far in that band, featured races included, divided by its **weight** in the band;
  4. not the category of the race at the same wall-clock time 1 day earlier;
  5. plan order.

  An hour that no main category fits stays without a race. It is an **empty hour**, and the proposal's notice counts it.
- **Weight.** A category's weight in an open band is its mean expected entrants over the band's weekday-hours. In a closed band, or a band with no hours, the weight is 0, and the share ratio of a category with weight 0 is infinite.
- **Between races.**
  - **Gap hours** of a date are its start times that hold no race, are outside every `block` event, and start 1 or 2 hours after one main race and 1 or 2 hours before the next, with no between race between those two main races.
  - **Due.** A between category is due in a band when its share ratio there is at most the band's **pace**: all races so far in that band, of every category and featured races included, divided by the summed weight of all plan categories in the band.
  - **Fit.** A between category fits a gap hour by the rules of *Fit* above.
  - **Order.** The date's gap hours are tried in band order, prime then normal then quiet. Within a band they go by value, highest first, and ties go to the earlier time. Each takes one of the between categories that are due in its band and fit, chosen by the rules of *Categories*, or stays free. Due and gaps are checked again before each gap hour, so a gap takes at most one between race.
  - A between category that finds no gap hour stays due. Between races never make a date short.
- **Shortfall.** A date that ends with fewer than N main races is in the proposal's **shortfall**, and its notice counts it.
- **Alternate weeks.** After featured, main and between races are all placed, and before modes are assigned, races of alternate weeks may move an hour, so a moved race's mode follows its new band.
  - **Moving weeks.** The period's Monday to Sunday weeks count from 1, the first maybe partial. Races of weeks 2, 4, 6 and so on may move. The others never do.
  - **Section.** A start's two-hour section is its Eastern Time hour halved, rounded down: 0–1, 2–3, … 22–23.
  - **Featured races.** A featured race never moves. It alternates between its two hours instead (see *Featured races*).
  - **Anchors.** Before the alternate weeks move anything, the earliest race of each every-hour category at each Eastern Time hour is its **anchor**. An anchor never moves, so the step never empties an hour a category had covered.
  - **Moves.** Any other race of a moving week may stay, start an hour earlier, or start an hour later. The new start must:
    - be on the same date, so 23:00 never moves past midnight and the daily and weekly limits cannot change;
    - exist, as daylight saving may skip an hour;
    - be outside every `block` event;
    - be in a band open to the category;
    - be outside every `avoid` event that lists the category;
    - be a prime-time start, if the race starts in prime time: it never leaves prime time.
  - **Rules kept.** Between consecutive races in time order:
    - the races keep their order;
    - main races stay at least 2 hours apart;
    - a between race with a main race 1 or 2 hours on each side keeps one there;
    - two races less than 2 hours apart may not have either inside a `single` event.
  - **Order.** Among the moves allowed, the plan takes the one with, in order:
    1. the most main races in a section that held no main race on the date 7 days earlier;
    2. the most races in prime time;
    3. the fewest short gaps, as defined under *Hours*;
    4. the fewest moves;
    5. the most races in normal time;
    6. the highest total value;
    7. race by race in time order: staying, then the earlier hour.

    A dynamic program finds it. The weeks before a moving week never move, so "a week earlier" is the placement as it was.
- **Modes.**
  - A featured race, and every prime-time race, of a plan category with one source runs its most popular mode.
  - Every other race takes its plan category's modes in turn, in list order and time order. So does every race of a mixed category, featured ones included, which makes a mixed category's featured mode rotate.

#### Results

- **Expected entrants** is the sum of $E$ with each race's own mode and that mode's category, less the clash.
  - The clash is counted for each two races less than 2 hours apart, as their pair score × the smaller of their two expected entrants. It is charged to the race with the fewer expected entrants, or to the later race on a tie.
  - A missing pair score counts as 1.
  - The clash charged to one race is at most its own expected entrants.
- **Expected cancelled rooms** is the sum of 1 − the fill of that category in the race's block.
- **Weekly repeats** counts the races whose category also ran at the same wall-clock time 7 days earlier, when neither race is a featured race.
- **Ranking.** The report ranks the four proposals by expected entrants per race, between races included, and marks the best one.
- **Races.** Every race of the proposal, and the between races among them.
- **Moved races.** The races that *Alternate weeks* moved an hour. The report counts them in each proposal's text line and in the note above its full schedule, which adds that featured races never move: they alternate between their two hours instead. A featured entry has no `moved` count. Each race of a proposal's schedule carries `moved`: -1 an hour earlier, 0 not moved, 1 an hour later. The CSV and JSON files leave it out.
- **Races by hour.** Each row of a proposal's `categories` has `by_hour`: 24 counts, the category's races starting at each Eastern Time hour (0–23) over the whole period, featured and between races included. The CSV and JSON files leave it out.
- **Races a day.** The fewest and the most main races on any date of the proposal.
- **Short gaps.** The proposal's consecutive main races less than 3 hours apart, unless both start in prime time.
- **Unbracketed.** The between races without a main race 1 or 2 hours before them and another 1 or 2 hours after them. Only a featured between race can be one.
- **Composition.** For each plan category, the report lists:
  - its modes with their factors, the most popular mode, retired modes, and modes with no slots in the window;
  - its limits, whether it is a between category, whether it is an every-hour category (`every_hour`), its open bands and its weights.

  It also lists the plan categories left out.
- **Uncovered.** Each proposal has `uncovered`: for each every-hour category, its target hours with no race over the period, an empty list when it covers them all. It is an empty object when the `every_hour` rule is off or no category is marked.
- **Seed.** The result's `parameters` carry `seed`.
- **Notices.** A proposal's notice counts its shortfall dates, its empty hours and its unbracketed races. When anything is uncovered, it adds a sentence like `Every-hour categories lack races at 2 hours: entrance 04:00; keys 22:00.`, in the form `Every-hour categories lack races at {N} hours: {category} {HH:00, HH:00}; {category} {HH:00}.`
- **Today's baseline** is the newest season that ended inside the history window, scored with the same model, next to its actual entrants and cancelled rooms.
- **CSV.** `--out DIR` writes `schedule-<set>.csv`, with the columns `date,time,category,mode,mode_id,utc`:
  - `date` and `time` are Eastern Time;
  - `category` is the plan category;
  - `mode` and `mode_id` are the alttpr.racing mode, whose own category the race counts under;
  - `utc` is ISO 8601.
- **JSON.** It writes `schedule-<set>.json` as `[{"time": "<unix seconds>", "mode": <id>}]`, the shape of the schedule API's rows.
- **Report.** `--html PATH` shows, for each proposal, its full schedule: one date-by-hour grid per Monday to Sunday week of the period, with every race in its hour and the featured and between races marked. Hovering a race says whether it moved an hour earlier or later.
  - Each featured entry carries `hours: [odd, even]`, and its text line reads like `entrance Tue 14:00/15:00 Crosskeys (9 races)`. The featured table has the columns Category, Weekday, Weeks 1, 3, 5… (ET), Weeks 2, 4, 6… (ET), Mode, Races and Skipped dates, under a note that each featured race alternates week by week between two adjacent hours.
  - After the Categories table, each proposal shows a Races-by-hour table: one row per plan category, columns 0 to 23 and Total, and an All row of the hour totals. Zero cells are blank, except a target hour that an every-hour category lacks: it shows a visible 0 with a tooltip, and the table's note says so. A category's cells are shaded by count.
  - The composition lists "every hour" among a category's limits. The method note says the hours are drawn with odds by value, and gives the seed. The text report gives the seed too.

#### Success rates

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
- **Band rule.** It pools each band over the three groups. A category that fires one time in ten is allowed. A band can be closed by bad luck in a category with few slots.
- **Hours before categories.** A date's hours are chosen before its categories, so an hour may stay empty when no category fits it. The notice counts such hours.
- **One date ahead.** A date looks only one date ahead, and the next date's best choice is counted without its own lookahead. Under the 3-hour preference outside prime time, the evening steps from the end of prime time to 20:00 or 21:00 and on to 23:00 or 00:00, so 19:00 and 22:00 stay thinner than their neighbours.
- **Coverage.** It is best effort. An hour that a category cannot get, for example under a tight `weeklyMax`, is listed in `uncovered` and the notice.
- **The draw.** Drawing costs some expected entrants against always taking the best-value hours, about 1–4% on today's archive. The model counts that cost and cannot see the variety it buys.
- **Between races.** The ladder has rarely run races an hour apart, so their clash cost comes from the Mode overlap score, which is an upper bound. Spoiler's scores are weak evidence and high, so its between races score low even though the plan file treats it as a category that shares few racers. The cap keeps a race between two others from losing more than its own entrants.
- **Featured weekdays.** With more than 7 plan categories, they cannot all have a weekday of their own.
- **Alternate weeks.** The demand model prices only what each move costs at its new hour. It never prices the racers a move reaches who are free only at hours a fixed weekly grid leaves empty. A section with no race within an hour of it on that weekday stays empty, as on Light's quiet early hours.
- **Cancelled rooms.** A cancelled room may have had one entrant. It counts as zero.
- **Mixed categories and old modes.** A mixed category is assumed to draw the mean of its modes, each like its own category at the same hours. A mode with no slots in the window, such as a long-retired one, is assumed to draw like its category's average.
- **Baseline.** It is scored by a model fitted on the same window, so it flatters the model. The proposals are scored out of sample.

## Schedule strategy comparison

`scripts/schedule_lab.py` breaks the [race schedule proposals](#race-schedule-proposals)' rules one at a time, in thirteen variants plus a combined fourteenth, and compares each with the shipped rules. It runs `scripts/schedule_planner.py` under other `Rules`, so it needs the same archive and schedule mirror. The printed report gives each variant's measures, verdicts and the recommendation.

```bash
python3 scripts/schedule_lab.py                      # the full comparison
python3 scripts/schedule_lab.py --variant no-between # one variant against shipped, no combined one
python3 scripts/schedule_lab.py --json lab.json      # also write every number
python3 scripts/schedule_lab.py --jobs 1             # no worker processes
```

- `--archive`, `--config`, `--start` and `--weeks` work as in the planner.
- `--variant` takes the key of one of variants 1 to 13. `combined` is not a choice: it only runs in the full comparison.
- The runner uses worker processes, one per CPU by default, which need POSIX semaphores. Inside a sandbox that denies them, use `--jobs 1`.

### In plain words

The planner keeps a fixed set of rules, and each rule may cost some turnout. Each variant breaks one rule and plans the same season, for all four sets, with the shipped rules as the comparison. The plan file's bands, composition and events stay as they are in every run: the bands are set by hand, so no variant ranks them.

The planner is deterministic, and the lab plans every variant with the default seed, so there is no search budget. A variant, a set and a fold run once.

A variant is judged on proposals scored out of sample, so a rule that only overfits the history cannot win. The planner computes the open bands (the band rule) and the featured record from the history it is given, so the lab hands it one half and both are out of sample too. The demand model also cannot see that a category's races draw fewer racers the more of them run on one date, so every race is scored with a correction for that. A variant that piles races onto the strongest categories would otherwise look better than it is.

The Weekly Entrance `avoid` event is an external fact, so it stays in every variant. Every variant plans the same period as the planner.

### Variants

Each of variants 1 to 13 breaks exactly one rule. The **lever** is what the lab changes: a field of `Rules` in the planner (`SHIPPED` holds the rules this section calls shipped), or a lab lever that is not a `Rules` field. A **model-blind** variant may gain from something the demand model cannot price.

| # | Variant | Rule it breaks | Lever | Model-blind |
|---|---|---|---|---|
| 1 | `open-bands`, Every band open | A category never races in a band where its rooms fired less than 10% of the time | `band_rule` off | Yes: a category races in bands where it almost never fired, and the model extrapolates |
| 2 | `no-featured`, No featured races | Each plan category gets one race in prime time, on a weekday of its own, alternating week by week between two adjacent hours | `featured` off | No |
| 3 | `fill-shares`, Shares by fill | A band is shared by expected turnout | `shares` is `fill`: by how often its rooms fired in the band | No |
| 4 | `equal-shares`, Equal shares | A band is shared by expected turnout | `shares` is `equal`: alike | No |
| 5 | `no-between`, No between races | Glitched and Spoiler are between categories: their races always start between two other races, never in a slot of their own, and come on top of the main races a day | `between` off: the plan file's between categories are main categories like the others | Yes: a between race's clash cost comes from Mode overlap scores, an upper bound that is weak evidence for Spoiler, so the model may overcharge the shipped rule |
| 6 | `two-hour-gaps`, Two-hour gaps | Outside prime time, 3 hours apart is preferred | `wide_gaps` off: main races are still at least 2 hours apart, but 2-hour gaps cost nothing | Yes: the model cannot see that racers join consecutive races more easily, or that a long race overlaps the next one, when races start 3 hours apart rather than 2 |
| 7 | `no-favourite`, No favourite mode | A category's prime-time races run its most popular mode | `favourite` off | No |
| 8 | `no-rotation`, No rotation | A race avoids the category that ran at the same hour a week earlier, then the one that ran there a day earlier | `rotation` off | Yes: racers do not tire of one category at a repeated hour |
| 9 | `no-plan-caps`, No plan caps | The plan file's `dailyMax`, `weeklyMax` and `onlyPrimeTime` | The caps are removed from the plan file. `between` and `everyHour` are not caps, so they stay, and so do the bands | No |
| 10 | `no-holidays`, No holidays | A holiday counts as a prime-group day, with that group's factors, bands and races a day | Lab lever `holidays` off: the planning model is fitted without holidays. The scoring model always counts them | No |
| 11 | `no-stagger`, No alternate weeks | In every second week, races move an hour to reach the two-hour sections the week before left empty | `stagger` off | Yes: the model cannot see the racers who can only join at hours a fixed weekly grid leaves empty, so it counts only the cost of moving races |
| 12 | `no-every-hour`, No every-hour floor | A category marked `everyHour` gets at least one race at every hour of its open bands | `every_hour` off | Yes: the model cannot see the racers who can only join at the hours a category would leave empty, so it counts only the cost of covering them |
| 13 | `no-draw`, Best hours | Each date draws its hours among the equally good sets, with odds by their value | `draw` off: each date takes its highest-value set again | Yes: the model cannot see the racers who can only join at hours the best-value grid never uses, so it counts only the cost of the draw |
| 14 | `combined`, Combined | Every rule that variants 1 to 13 broke and won | The levers of the combining variants, merged | Marked when any part is |

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
  - picked hours that no category could take;
  - hours an every-hour category lacks, judged by the plan file's `everyHour` flags whatever the variant's rules, so `no-every-hour` shows the gaps it leaves.

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

`tests/analyses/test_schedule_lab.py` tests all of this on the synthetic four-category world in `tests/analyses/schedule_fixtures.py`, without the network: each variant's levers, the halves, the saturation table, hand-checked scores, the verdict, guard and combined rules, and a smoke run of every variant.

### Limits

- **Observational.** The turnout and fill rates are what past slots drew. They do not control for announcements, competing events or player availability.
- **Saturation.** It is measured from history, where a category rarely had more than 3 races a date. The 4 or more bucket rests on few slots.
- **Model-blind variants.** Open bands extrapolate to bands where a category almost never fired. No rotation assumes racers do not tire of a repeated hour. Two-hour gaps assume racers join races as easily 2 hours apart as 3, and that a long race does not overlap the next. No between races leans on a clash cost that is an upper bound and, for Spoiler, weak evidence. No alternate weeks cannot see the racers who can only join at hours a fixed weekly grid leaves empty, so it counts only the cost of moving races. No every-hour floor cannot see the racers who can only join at the hours a category would leave empty, and Best hours cannot see those who can only join at hours the best-value grid never uses. Their gains need a real trial.
- **Noisier halves.** Each half holds half the history, so its model, its band rates and its featured record are noisier than the whole year's. That is the point of scoring out of sample, but it costs precision.
- **Determinism.** The planner is deterministic, so each variant, set and fold runs once. Every variant is planned with the default seed, so all variants see the same draw key at the same start time. The lab does not average over several seeds, so a verdict near the 0.5% tolerance may still be draw noise, and only two folds back each verdict.
- **Judgement calls.** The 75% guard and the 0.5% tolerance were chosen, not derived.

## Rating comparison

`scripts/rating_lab.py` compares other rating algorithms with the [ladder rating](../README.md#ladder-rating), on the pairs of its [backtest](../README.md#backtest) and under the ladder's promises. The ladder's $\beta$, $\sigma_0$ and $\alpha$ come from this lab's retune. Throughout this section, **shipped** means the lab's baseline, the rating before that retune ($\beta = 12$, $\sigma_0 = 8$, floor 4, no damping), which the lab keeps so the comparison stays reproducible.

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

### How the candidates are tested

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
