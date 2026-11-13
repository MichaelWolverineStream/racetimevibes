# The dashboard

- **Purpose:** what the page in `site/` shows, and what it computes in the browser from the published datasets.
- **Read before:** changing anything a visitor sees. The code conventions for `site/` are in [.claude/rules/site.md](../.claude/rules/site.md).
- **Code:** `site/index.html`, `site/*.js`, `site/*.css`. The datasets it reads are in [datasets.md](datasets.md).

Everything here happens in the browser: the scripts in `site/` read the datasets and compute the rest on the page. Data freshness is build-time: the dashboard trails racetime.gg by up to about two hours, the interval of the [scheduled data refresh](operations.md#scheduled-data-refresh). The browser reads the local data files and never contacts racetime.gg.

## Page layout and navigation

The page runs top to bottom:

1. The [Top-rated banner](#top-rated-banner).
2. The [Upcoming races](#upcoming-races) strip.
3. **01 Latest**: the newest results with their tags.
4. **02 Racers**: the leaderboard, with its season select and the rating column.
5. **03 Records**: the hall of records.
6. **04-07**: the Ladder Data panels, on the schedule, participation, traffic and turnout.

Each racer also has a page with their ratings, badges, latest results, and charts (see [Racer pages](#racer-pages)). [Face Off](#face-off) is a page of its own that sets two racers side by side.

The masthead holds the category artwork, the title block, a racer comment, the visitor counter and the theme toggle, with their tops aligned.

- **Data snapshot** ends the title block: the age of the published data, "just now" under a minute, then whole minutes, hours or days, counted from when the data was last built and updated every minute. Hovering or tapping it shows the exact UTC time.
- **The racer comment** is a random comment of at least 12 characters from the last 30 days, or from any time when those 30 days hold fewer than 20. It is cut at 180 characters, and the bubble is at most 450px wide. **↻** (Another comment) picks another.
- The visitor counter hides at 1199px and below, and at 980px and below the comment moves to a row of its own under the artwork and title.
- **The theme toggle** ends the masthead on every page and at every width. It is a button labelled "Dark mode", pressed in the dark theme, and shows a moon in the light theme and a sun in the dark one. Once a Piece of Heart is found, the heart sits above it (see [Easter eggs](#easter-eggs)).
  - A first visit follows the system's light or dark setting, and keeps following it until the toggle is used.
  - The choice is kept in this browser's local storage, or for the visit when storage is unavailable, and other open tabs follow it.
  - The page cross-fades into the other theme, or switches instantly with reduced motion or in a browser without view transitions.
  - The theme is set before the page first paints, so it never flashes the other one, and the browser's toolbar colour follows the nav bar.

A nav bar under the masthead links to the seven sections, grouped as Racing (01-03) and Ladder Data (04-07), whose links are Schedule, Participation, Traffic and Turnout, with the Battle group between them. Thin dividers separate the groups, side by side on wide screens and one above the other at 980px and below. Ladder Data is hidden on phone-width screens, and the whole bar is hidden on racer pages. The top-rated banner and the Upcoming races strip sit between the nav bar and Latest, and are hidden on racer pages and on Face Off.

- **Battle:** a caption over one link, **Face Off**, drawn as a pill split diagonally into the [Face Off](#face-off) card's red and blue, deepened so its text keeps 4.5:1, with a gold edge. It is a page, not a section. Its text shares the other links' line, and a sheen crosses it once when the page loads, except with reduced motion. On hover it lifts slightly. While Face Off is open it carries `aria-current="page"` and a second gold edge inside the first. It shows at every width, including phone width where Ladder Data hides.
  - **Width:** each caption takes no width of its own, so it never widens its group. At 1199px and below the groups' side padding narrows, so the three groups keep one row down to 981px.
- **On Face Off the nav stays visible**, so the pill marks where you are. A section link there returns to the board and scrolls to that section.

## Top-rated banner

The banner crowns the top-rated racer in each category: **Overall**, then every mode in mode order except `unknown`, which only groups races whose mode could not be read. The featured daily racer uses the same categories.

- **Scope:** a two-button toggle in the banner header, in a group labelled "Rating period": the current season, pressed by default and labelled like "Season 10 · TR" with the full name in its tooltip, then **All time**. Toggling redraws the tiles in place, and the choice is not persisted. With no seasons there is no toggle and the banner is all-time. At phone width the toggle leaves the border for a row of its own above the tiles.
- **Who:** a tile shows exactly the top of the leaderboard sorted by rating in its category and scope: the season's rating under a season, the all-time rating under **All time**. There are no filters and no decay, so a leader keeps the crown until someone rates higher. A season tile shows the season rating, and runners-up use the season's ranks.
- **Ties:** ranks are competition ranks, so racers tied at the top share one tile. It shows up to three avatars, names the first two in name order, and adds **+N** for the rest, whose tooltip names them.
- **Tile:** the category, the avatar, the name and the rating. Each name links to that racer's page, opened on the tile's mode (**All modes** for Overall). Racer pages are all-time whatever the scope. A category where nobody is rated in the scope has no tile.
- **Runners-up:** under the leader, the next five racers in the category, as small avatars side by side, in rank order and then name order. Tied leaders are not repeated there. Hovering or focusing one shows its rank, name and rating, such as "#2 Alice · 1834", and clicking it opens that racer's page on the tile's mode. A category with fewer rated racers shows fewer.
- **Layout:** one row that keeps names whole and scrolls sideways when it is wider than the screen.

## Season names

Every season label on the page carries the season's name from `season_names` (see [Season fields](datasets.md#season-fields)): the season select, the top-rated toggle, the rating and rank tooltips and the Seasons cards. Visible text uses the short form, "Season 10 · TR" or "Season 11 · Panda", and just "Season 12" while the season has no name. Tooltips and titles use the full form, "Season 10 · Turtle Rock". The wording "the Season of the …" is used only for a champion name.

- **Retro names:** Seasons 1-10 carry fixed names, the short and full name of an ALTTP dungeon, from Eastern Palace (EP) to Turtle Rock (TR).
- **Champion names:** from Season 11, a season is named after an animal chosen by the previous season's overall champion.
- **Named by:** a champion season's namers are the previous season's overall #1 by season rating, with ties all credited. It is derived on the page and shows only once that previous season has ended, and it can change until that season is [locked](datasets.md#season-locking).

## Upcoming races

A strip between the top-rated banner and Latest results, hidden on racer pages and Face Off. It lists up to 5 slots still to start from `upcoming`, all from the season of the next slot.

- **Title:** "Upcoming races in the Season of the Panda" for a champion season, else "Upcoming races in " and the season label.
- **Full Schedule:** a link on the strip's top border, opposite the title, opens `https://alttpr.racing/calendar` in a new tab. At phone width it sits under the title instead.
- **Slot:** the Eastern Time weekday and start time, a countdown rounded up such as "in 1 d 6 h", the mode's dot and the schedule's mode name.
- **Refresh:** it redraws every minute, so countdowns tick and a started slot drops off. It is hidden when no slot is left.

## Latest results

- **Feed:** the newest races first, 20 at a time, with **Show 20 more** for the next 20.
- **New since your last visit:** the header counts the races added since this browser last loaded the page, as "N new since your last visit", followed by how many of them carry each tag: PB, Preset PB, Mode record, Preset record, First win, Upset, Photo finish, Milestone and Badge, with ×N for repeats. Each of those races has a "New" dot. The marker stays put when the same tab reloads, and the header shows nothing when no race is new. The browser stores the newest race it has shown in local storage, and the tab's starting point in session storage.
- Each race shows its [race tags](highlights.md#race-tags). Expanding a race lists each entrant with their change in the season's rating in that race's mode: `+12`, `−9`, or `±0`, with a true minus sign and no arrow. ▲ and ▼ are for rank movement only, so a points change never looks like a rank change. The change is the entrant's season-mode points after the race minus their points before it, or minus the starting 1000 in their first race of that mode in that season. Its tooltip reads, for example, "Season 10 · Turtle Rock · Keys rating after this race: 1234". A race with no season shows no change.
- **Medal colours:** a finisher's place pill takes the colour of the [medal](definitions.md#entrants) that place earns in that race's field, and a place without a medal stays plain. The same applies to the winner shown on each race row and to the racer page's own rows.
- **Entrant tags:** in an expanded race, each entrant's row shows the race tags that name that entrant, between the name and the finish time, in race-tag order. Each tag stands alone there, with the same label and tooltip it would have for one racer on the race row, so Upset and Photo finish appear on the winner. At phone width the CR and CPH drop to a line under the name, and the tags to a line under that, so the name keeps its width.
- **CR and CPH** (see [Chest counts](datasets.md#chest-counts-cr-and-cph)) appear only where races are listed row by row: each finisher's row in an expanded race, and each of the racer's own rows in a racer page's [Latest results](#racer-pages).
- **Sidebar:** **Your racers**, the **Featured daily racer**, **Fact of the day**, **Latest badges** and **Latest avatars**, in that order. At 1199px and below it collapses behind a **Racers & highlights** toggle, closed when the page loads; at 980px and below it sits above the feed.
- **Your racers** lists the racers followed in this browser (see [Following](#following)), each with their 7-day rank movement (see **Ranks** under [Racer leaderboard](#racer-leaderboard)). With none it reads "☆ Star a racer to follow them here."
- **Latest badges** lists the 12 most recent unlocks by anyone, however long ago. Clicking a badge opens its holders panel, as on a racer page.
- **Latest avatars** shows the 6 racers whose avatar changed most recently, newest first, each with how long ago the change was first seen. The section is hidden when there are none. What counts as a change, and how it is dated, is under [Avatar changes](avatars.md#avatar-changes).

### Featured daily racer

The Latest sidebar's **Featured daily racer**, between **Your racers** and **Fact of the day**, spotlights one racer a day.

- **Candidates:** racers named by a race tag (see [Race tags](highlights.md#race-tags)) in races from the 14 Eastern Time calendar days before today. Today's races do not count. A tag names the racer it is about: the record setter, the first-time winner, the upset or photo-finish winner, the PB or Preset PB setter, the milestone racer or the badge earner.
- **Exclusion:** a racer in the top 3 of any category (see [Top-rated banner](#top-rated-banner)) at the end of yesterday is not a candidate, in either scope: all time or the current season. Ranks are competition ranks, so ties can put more than three racers in a top 3.
- **Pick:** candidates are listed in name order. The pick starts at the candidate given by the 32-bit FNV-1a hash of `featured YYYY-MM-DD`, using today's Eastern date, modulo the number of candidates. It steps forward, wrapping around, past anyone who has climbed into a top 3, in either scope, since.
- **Stability:** everyone sees the same racer all day. A data refresh changes the pick only when the featured racer reaches a top 3, in either scope, that day. A page left open past midnight keeps its racer until it is reloaded.
- **Card:** the **Your racers** card, with its rank movement, and up to two more lines. The section is hidden when there are no candidates.
  - **Highlight:** the racer's latest highlight in the window and how long ago it was, such as "★ 250th race · 11 days ago".
  - **Claim to fame:** the racer's [claim to fame](highlights.md#claim-to-fame), such as "✦ 1 of 7 racers with 7+ wins in a row". A count of 1 reads "✦ Ladder best: …" instead. A badge claim shows the badge's art and name, and clicking it opens the badge's holders panel. A racer without a claim has no such line.

### Fact of the day

The Latest sidebar's **Fact of the day**, between the **Featured daily racer** and **Latest badges**, shows one of the [facts](highlights.md#records) a day.

- **Rotation:** with N facts, every fact shows once in each cycle of N days, and a fact that shows is not repeated for the next 7 days.
  - Day D counts the days from 1970-01-01 to today's Eastern date, and the cycle is ⌊D / N⌋.
  - A cycle's shuffle is the fact indexes sorted by the 32-bit FNV-1a hash of `CYCLE INDEX SEED`, ties going to the lower index. The seed is `fact of the day 2`, set as `FACT_SEED` in `feed.js`. Changing it picks a new schedule from that day on.
  - **The 7-day rule:** the 7 facts that close the previous cycle's shuffle move to the back of this cycle's first 14 slots, keeping their order, so none of them can show in this cycle's first 7 days.
  - **Why the rule needs no history:** only those 14 slots move, so every cycle closes exactly as its shuffle does, and nothing about past days is stored.
  - **Fewer facts:** the rule needs at least 21 facts. With fewer, the 7 becomes ⌊N / 3⌋ and the 14 twice that.
  - Day D shows the fact at position D mod N of the result.
  - The rule holds while the list of facts stays the same. A fact that appears or drops out changes N and reshuffles the schedule, so in the week after that, a fact can repeat early.
- **Stability:** everyone sees the same fact all day, though a data refresh can update its numbers.
- **Highlight:** the fact's headline number is set larger, in the display face.

## Racer statistics

The browser computes these from `participants.json`. The leaderboard, the racer pages and the player card all use these definitions.

- A racer counts as finishing a race only when they are a [finisher](definitions.md#entrants): their entrant `status.value` is `done` **and** the API reports a `place`. Everything else counts toward DNF, except `dq`, which is tracked separately on the racer page.
- **Diamond, Gold, Silver and Bronze** count [medals](definitions.md#entrants), which depend on the field size: a field of 10 or more gives diamond to first and gold, silver and bronze to second to fourth, a field of 6 to 9 gives gold, silver and bronze to the top three, a field of 4 or 5 gives gold and silver, and a field of 2 or 3 gives gold to the winner. A field of 1 earns none. Each column header's tooltip states the rule.
- **Median time** covers finished races only and comes from `finish_time`, rounded to whole seconds (the rating, tags and records use the exact fractional time). With an even number of finishes it is the mean of the middle two. Racers with no finishes show `--` rather than zero.
- **Win %** is the share of races won outright: first places in any field divided by races entered. A race that ended in a DNF still counts in the denominator.
- **Head-to-head** pairs every racer in a race against every other racer in that same race. The lower `place` wins. Any finisher beats any non-finisher. Two non-finishers draw.
- **H2H win %** is wins divided by wins plus losses across every pairwise matchup. Draws are reported in their own column on the racer page and are excluded from the denominator. This normalizes for field size: finishing fifth of ten is five wins and four losses, so a racer who never wins outright can still rate highly by consistently placing near the top of large fields.
- **Trend** compares the win rate over the last five decisive meetings against the career rate with that opponent, and appears only after more than five decisive meetings. A move of more than five percentage points shows as up or down; anything smaller shows as steady.

## Racer leaderboard

- **Season:** a **Season** select above the table picks the scope: every season, newest first and the current one by default, then **All seasons**. Options read like "Season 10 · TR" (see [Season names](#season-names)), with the full name as tooltip. With no seasons the select is hidden and the board covers the whole archive. Changing it resets the sort to the default.
  - Under a season, every column except Badges is calculated from the races of that season, and of the selected mode when one is picked, with the rules under [Racer statistics](#racer-statistics). That includes H2H win %.
  - Under **All seasons**, every column covers the whole archive.
  - A racer with no race in the scope has no row. The table's status text names the season.
- **Rating:** by default the board is sorted by the rating, highest first, with ties broken by name. The Rating column follows the selected scope and mode. Under a season it shows the [season rating](../README.md#season-ratings), overall under **All modes** and else in the selected mode. Under **All seasons** it shows the all-time [Ladder rating](../README.md#ladder-rating), overall or in the selected mode (see [Mode ratings](../README.md#mode-ratings)). Its tooltip names the scope, such as "Season 10 · Turtle Rock rating · Keys" or "All-time rating". A racer is provisional, or unrated, in a scope by the rules of that scope. There is no minimum-races cutoff.
- **Position:** the number beside each racer is the row's position as shown, so it renumbers whenever a sort, filter or search changes the table. The top three positions shown get gold, silver and bronze.
- **Rank movement:** while the board is sorted by rating, highest first, a ▲ or ▼ beside the number shows how far the racer has moved over the last 7 days in the ranks the Rating column shows: the selected scope's overall ranks under **All modes**, else the selected mode's. **NEW** marks a racer who had no rating there 7 days ago. Under a season that is their first race in the season, or in the season's mode, in that window, so in a season's first week everyone in it is **NEW**. A season that ended more than 7 days ago shows no movement.
- **Ranks:** rating ranks are competition ranks: one more than the number of racers rated strictly higher, so tied racers share a rank. The racer page's rating strip uses the all-time overall ranks. The **Your racers** and **Featured daily racer** sidebar cards show 7-day rank movement in the current season's overall ranks, as ▲N, ▼N or NEW, with a tooltip naming the scope, such as "Season 10 · Turtle Rock rank, last 7 days". With no seasons they use the all-time ranks.
- **Columns:** Diamond, Gold, Silver, Bronze, Median time, Win % and H2H win % are defined under [Racer statistics](#racer-statistics). **Badges** counts the badges a racer has earned, across all modes and the whole archive, whatever season is selected.
- **5+ races only** is an optional button that hides racers with fewer than five races. It counts races in the selected season and mode, so a veteran with only a few races in one mode, or in one season, drops out of that board.
- **Raced in last 30 days** is an optional button that hides racers with no race in the currently selected mode during the 30 Eastern Time calendar days ending today. Any entry counts, including a DNF or DQ. It only hides rows: the stats shown stay those of the selected season.
- **Followed** is an optional button that shows only the racers starred in this browser. Unstarring a racer while it is on drops their row at once.
- **Find a racer** keeps the rows whose display name contains the typed text, ignoring case but not accents. It combines with the season, the mode and the buttons above.
- **Follow star:** each row has a star (see [Following](#following)).
- **Narrow screens:** at 980px and below, the first column stays put while the others scroll sideways, and a soft shadow marks the side where more columns are hidden. At phone width long names in that column end in "…". The racer page's presets and head-to-head tables do the same, except that preset names wrap instead.

## Following

A star, ☆, follows a racer in this browser, and ★ unfollows them. Stars sit in the leaderboard rows, on the Latest sidebar's racer cards and in a racer page's heading; each is a toggle button labelled "Follow <name>" or "Unfollow <name>". In a leaderboard row an empty star shows only while the row is hovered or focused, and always on a touch screen. Following is kept in this browser's local storage, up to 50 racers, and in memory for the visit when storage is unavailable. It drives **Your racers** and the leaderboard's **Followed** button.

## Hall of records

Records runs in four parts. Only By mode follows the mode picker.

- **Seasons** has a card per season with a race, oldest first, plus the next season once it is named but has no race yet, marked **Upcoming** and dated by its first slot. A season that has not ended is marked **Current**. Hidden with no seasons.
  - A card shows the season number, a title, its dates, the Champion(s) of an ended season and, for a champion season, who it was **Named by** (see [Season names](#season-names)). Dates are a range, or "Since …" for the current season.
  - The title is the retro short name, quiet, with the full name as tooltip and accessible description, or "The Season of the Panda" in the accent colour. Clicking it selects that season on the leaderboard and moves focus to the leaderboard heading.
- **Across all modes** holds the record cards and the streak boards (see [Records](highlights.md#records)): Days, Wins, Podiums and Finishes in a row, up to 5 racers each.
  - A toggle switches the boards between **Active now**, the default, and **All-time**.
  - **Active now** ranks every racer's current streak of two or more: a daily streak whose last day is today or yesterday in Eastern Time, or any other kind whose latest race is within the last 30 days. A board with none reads "None active".
- **Record progression** has a step chart per mode, on one shared date axis with each mode's own time axis. The charts sit three to a row on wide screens, two at 1100px and below, and one at phone width. Each header shows the current record, and the chart draws the first recorded time hollow, each break filled, and the current record with a halo.
- **By mode** has a mode picker that drives the current-record summary strip, the finish-time violins of the mode's presets, and the presets table, whose Gold median column is the preset's median winning time (see [Finish-time violins and preset tables](#finish-time-violins-and-preset-tables)). It opens on the most-raced mode.

## Finish-time violins and preset tables

- Each dot is one timed finish: an entrant whose status is `done` with a `place` and a `finish_time`. DNF, DQ, and untimed entries are not plotted.
- The finish-time violin charts draw one violin per seed preset, side by side, with finish time on the vertical axis. Each violin holds every racer's timed finishes in that preset across the whole archive. The shape is a Gaussian kernel density estimate with Silverman's bandwidth, $0.9 \cdot \min(s, \mathrm{IQR}/1.34) \cdot n^{-1/5}$, using $s$ alone when the IQR is zero, cut at the fastest and slowest observed finish. Each dot is spread sideways within the shape by a fixed pseudo-random offset, so dots keep their place across redraws. A preset whose finishes all share one time shows dots without a shape.
- The bold line marks the preset's median time and the gold line its median winning time. Both come from the preset statistics (see [Presets](highlights.md#presets)), which use exact times, so they match the presets table to the second. The median's tooltip also gives the finishes, the racers, and the quartiles, using the same median-of-halves definition as the dashboard's other quartiles.
- **Which presets:** a preset is charted only with 10 or more races in total, and a chart shows at most 8. The chart's ranking picks the 8; they are then laid out in the fixed mode order, keeping the ranking within a mode. A chart always has 8 equal slots, so a violin is the same width in every chart, and fewer violins sit centred. When presets are left out, a note above the chart says how many.
  - **Hall of records › By mode** charts the picked mode's presets, ranked by total races, then by name. The presets table below it lists every preset.
  - **Racer pages** chart the presets the racer entered most, DNFs included, in the mode picked on the page, or across every mode under **All modes**. Ties go to total races, then name. The racer's finishes are highlighted.
- Each violin takes its mode's color, and each mode keeps one color everywhere. The eight hues come from a palette checked for color-vision deficiency in this fixed left-to-right order, which is why the violins follow mode order rather than speed. When a chart mixes modes, each violin's label also names its mode, so color is never the only cue.
- Preset names wrap onto two lines and end in an ellipsis if they still don't fit; every tooltip gives the mode and the full name. A chart narrower than 880px keeps that width and scrolls sideways.
- On a touch screen, a tap on a median or gold line reads that line, and any other tap reads the nearest finish within about 16px.
- **Racer presets table:** below the violins, a racer page lists every preset the racer entered in the picked mode, in the same sortable table as Hall of records › By mode. Under **All modes** it lists every mode's presets and adds a Mode column. Every column is the racer's own, defined like the preset statistics (see [Presets](highlights.md#presets)) but over the racer's entries only: **Races** entered, DNFs included; **Median time** of their timed finishes and **Gold median** of their timed wins, each rounded to the second; **DNF %**, their entries that are not finishers; **Avg racers**, the mean field size of those races; and **Fastest**, their best time, linked to its race, the earliest on a tie. The Hardest tag still marks the category-wide hardest preset. The table opens sorted by races, in the violins' ranking, and goes back to that whenever the mode or the racer changes.

## Ladder data panels

Sections 04-07 chart `race-stats.json`. [Schedule and participation metrics](datasets.md#schedule-and-participation-metrics) defines every number they show, and [Schedule](definitions.md#schedule) defines the slots they count.

- **Time range:** a toolbar above the panels picks **All data**, the default, or one calendar quarter, labelled "· partial" when the archive does not cover all of it. It sets the 04 heatmap and shades the selected quarter's months in the 05 charts. The 06 and 07 season charts always show every season, and the leaderboard has its own season select. The toolbar also shows **Scheduled races counted through** the Eastern Time date and hour in `source.schedule_counted_through`, the last slot the schedule panels count. Races later than that are not in them.
- **04 Primetime heatmap:** a 7 by 24 grid of weekday against Eastern Time hour. At 980px and below it turns, with the hours down the side and a column per weekday, so it never scrolls sideways. A cell's colour is its band, primetime, normal or quiet, and its text is the racers per scheduled race to one decimal, or "·" when nothing was scheduled. The legend adds "Fewer than 5 scheduled races" for the cells without a band. A cell's tooltip gives its band, the racers per scheduled race, and how many of its scheduled races started.
- **05 Turnout over time:** three monthly charts, the average racers per finished race, the race entries per scheduled day and the races fired per scheduled day, counting only scheduled races that started. Months without a scheduled day break the second and third lines. The tooltips give the entries or fired races and the scheduled days behind each value, and the table lists the scheduled days, entries per day and fired races per day. The three charts share a row down to the 980px breakpoint without scrolling sideways.
- **06 How often races start:** a bar chart with one bar per season, showing the share of its scheduled races that started, on a 0-100% axis. The season in progress is muted and marked "(in progress)". The tooltip and table give the scheduled and started counts and the start rate.
- **07 Entries by season:** a bar chart with one bar per season, showing the season's total race entries. The in-progress season is muted. The tooltip and table add the races and the average racers per race.
- The bands are observational leads for schedule experiments, not causal estimates. They do not control for announcements, competing events, holidays, organizer effects, or player availability, and cells with few scheduled races may look extreme by chance. Use the primetime cells to design prospective schedule tests and compare repeated outcomes before making permanent changes.

## Racer pages

- **Heading:** the top line has **← All racers** at the left, and **Follow**, with its star (see [Following](#following)), **Face off** and the **Create player card** button (see [Player card](#player-card)) at the right. **Face off** opens [Face Off](#face-off) with this racer in the red corner.
  - **← All racers**, like the browser's Back, returns to the board scrolled to where it was left, or to the top of the board when the visit began on a racer page.
  - **Profile:** under it, a column 450px wide, narrowing to 320px before the stats move, holds the name and, under the name, the avatar beside a speech bubble with the racer's newest comment. The avatar and the bubble share a vertical centre, and the bubble's mode, place and race day sit under it. A comment over 80 characters is cut with "…", and hovering or tapping the bubble shows the whole comment. **↻**, right after the bubble, steps back through their older comments. A racer without comments has no bubble.
  - **Stats:** a card right of the profile, from the name's top to the panel's right edge, under the actions. A sheen crosses it once when the page opens. Its top row has Podiums, H2H Win Rate and Badges, each a label over a value. Its bottom row has Nemesis and Favourite Victim, each with their avatar and the racer's W-L against them, the Nemesis's in red and the Favourite Victim's in blue.
    - Podiums gives the racer's diamond, gold, silver and bronze medals in medal colours, with a tooltip naming each count.
    - H2H Win Rate and Badges each have a thin bar under the value, filled to the win rate and to the share of badges earned. A win rate of "--" has no bar.
    - Below 980px wide the card moves under the profile. Below 620px Podiums takes the top row, with H2H Win Rate and Badges side by side under it, and each rival takes a line of its own.
  - Every stat covers all modes and the whole archive, whatever mode is selected. Among opponents with at least five decisive meetings, **Nemesis** is the one with the lowest head-to-head win rate below 50%, and **Favourite Victim** the one with the highest above 50%; ties go to the opponent met more often.
- **Rating strip:** the racer's current all-time rating overall, then in every mode they are rated in, in mode order. Each tile gives their rank out of every racer rated there, marked provisional below 5 finishes there, and its tooltip explains the rating.
- **Badges:** earned badges come first in the order they were won, oldest first, and locked badges follow in table order (see [Badges](highlights.md#badges)). Earned badges animate, and locked badges show the grey still. A counter badge fills its border as it progresses. Hovering a badge shows the share of racers who hold it, to two significant digits, counted on the page from the current data. Clicking a badge, here or in the Latest results sidebar, opens a panel with every racer who holds it, earliest unlock first.
- **Latest results:** every race the racer entered, newest first, under a heading per month: the 5 latest at first, then 20 more at a time with **Show 20 more**. It covers every mode, whatever mode is selected.
  - Each row is the racer's own result: the date and Eastern Time start hour, the mode, the preset, their place (blank for a DNF or DQ), their comment, their finish time or DNF or DQ, the field size, their CR and CPH, the race tags that name them, and their change in the season's rating in that race's mode, shown as in [Latest results](#latest-results).
  - The comment fits on one line, and hovering or tapping it shows the whole comment. Up to four tags show, then +N. Tags follow the entrant-tag rule under [Latest results](#latest-results), so Upset and Photo finish appear only on races the racer won.
  - Clicking a row expands the whole race exactly as Latest results does, with the racer's own row highlighted.
- **Mode picker:** it sits below the rating strip, the badges and Latest results. Everything above it covers all modes, and everything below it follows it.
- **Rating chart:** it is all-time. Its heading gives the racer's rank and rating in the picked mode, overall under **All modes**, out of every racer rated there, marked provisional below 5 finishes there.
  - The chart has one point per race day: the racer's rating in the picked mode after their last race that day. With a mode picked it covers only that mode's race days and takes the mode's colour.
  - Like the record charts it is a step line that holds each rating until the next race day and runs on to the latest race in the archive. It draws the first race day hollow, the current rating with a halo, and the starting rating of 1000 as a dashed line.
  - Pointing anywhere in the plot snaps to the nearest race day, whose tooltip gives that day's change and races. On a touch screen a tap reads one race day and a sideways drag moves through them, while a vertical drag still scrolls the page. There is no race-day calendar.
- **Below the rating chart**, the picker also drives the finish-time violins of the racer's presets, the racer presets table (see [Finish-time violins and preset tables](#finish-time-violins-and-preset-tables)), and the head-to-head table, whose Win chance column always uses the overall rating. **Find an opponent** filters the head-to-head table by name, matching as **Find a racer** does.
  - **Face off:** the table's last column, a plain header that does not sort, holds a **VS** link per opponent that opens [Face Off](#face-off) with this racer in the red corner and that opponent in the blue.

### Player card

The **Create player card** button beside **Follow** on a racer page opens the **Player card** panel with a 600 × 300 PNG of the racer's standing. The browser draws it from the loaded data, and nothing is uploaded. It takes the page's colours when the panel opens, so a card made in the dark theme is dark. The panel offers up to four actions:

- **Copy image**, only where the page is a secure context and the browser can write images to the clipboard (`ClipboardItem` and `navigator.clipboard.write`).
- **Download**, always. The file is named `<name>-step-ladder-<data date>.png`. `<name>` is the racer's name lowercased, with accents dropped and every other run of characters that are not letters or digits turned into one `-`, or `racer` if nothing is left.
- **Share…**, only where the browser can share files (`navigator.canShare({ files })`), mostly on phones.
- **Copy link**, the racer page's address, only where the page is a secure context and `navigator.clipboard.writeText` exists.

Like the racer page's heading stats, everything on the card covers all modes and the whole archive.

- **Identity:**
  - The avatar, or the page's tinted letter when the racer has none.
  - The name, shrunk and then shortened to fit.
  - The month of the racer's first race.
- **Standing:**
  - The rating rank, out of all rated racers.
  - The rating, labelled provisional below 5 finishes.
  - Under the rank, the 7-day rank movement in the all-time overall ranks, as on the leaderboard under **All seasons**: ▲, ▼ or new.
  - A racer without a rating shows Unrated.
- **Rating line:** the overall rating's race-day step line, as the rating chart draws it under **All modes**, with the starting rating dashed and the current rating haloed.
  - The highest rating is marked **Peak** while the racer is below it. **All-time high** shows when they are on it.
  - The line needs two race days. Without it, the four numbers spread across the card.
- **Numbers:** races, wins (first places), podiums (medals) and H2H win %, as on the leaderboard and the racer page's heading.
- **Badges:** earned badges only, rarest first by holder count. Badges held by equally many racers keep the order they were won.
  - The row holds 15 tiles. With more than 15 badges, the last tile reads +N for the rest.
  - The rarest badge is named with its holder share.
  - A racer with no badges sees instead the locked counter badge with the largest share of its target done, with a progress bar. With no progress anywhere, the card says "No badges yet".
- **Nemesis:** the racer page's Nemesis, with the racer's W–L against them. Favourite Victim is left off, so a shared card never singles out a racer the sharer beats.
- **Footer:** the site name and address, and the date of the data snapshot in Eastern Time.

## Face Off

A page that sets two racers on one mirrored card. It computes everything in the browser from `participants.json` and `comments.json`; nothing new is published. `site/faceoff.js` and `site/faceoff.css` draw it, and `leaderboard.js` owns its routes.

- **Route:** `#/faceoff` is two empty corners, `#/faceoff/<id>` has the red corner picked and `#/faceoff/<idA>/<idB>` both, with racer IDs as in racer page addresses. An empty red corner with a blue racer reads `#/faceoff//<idB>`. An unknown ID leaves its corner empty, and the same racer in both corners keeps only the red one.
- **Picks replace the history entry**, so Back leaves Face Off instead of stepping back through picks. Opening Face Off from another view moves focus to its heading, and picking on the page keeps focus in the picker or on **⇄ Swap corners** and the page where it is. A **Most met** chip or **Random rivalry** moves focus to the new racer's avatar, since the render removes the button. The page title and the panel heading read "A vs B" once both corners are filled, and a status line announces the pair.
- **Corners:** the red corner is on the left and the blue on the right. Each holds an avatar that links to the racer's page, a name picker and the racer's rating. The VS between them carries the **⇄ Swap corners** button.
- **Pickers:** each name is a combobox. Focusing it selects its text and opens the options.
  - **Matching** is the rule of **Find a racer** (see [Racer leaderboard](#racer-leaderboard)), with names that start with the text first and the rest in name order. It lists up to 8 options, each with its avatar, and never offers the racer in the other corner. An empty input lists the followed racers (see [Following](#following)).
  - **Keys:** ↓ and ↑ move through the options and wrap, Enter picks the highlighted one or the only one, and Escape closes the list. A second Escape puts the picked name back. Leaving the field also restores it.
  - On a phone the input text is at least 16px, so iOS does not zoom the page. Options are 44px tall on a touch screen.
- **Empty card:** both corners show a "?" silhouette, the prompt "Pick two racers to face off" and **Random rivalry**, which picks one of the 25 pairs who have raced each other most, in a random order of sides. With one corner picked, the empty corner's input reads "Pick a challenger".
- **Most met:** with one corner picked, the empty corner lists chips for the picked racer's 5 most-met opponents, all modes and all time, most races first and then by name. A chip shows the opponent's avatar and name, and picks them.
- **Rating:** under each name, the all-time overall rating, the rank as "#N of M" and "provisional" below 5 finishes (see [Rating strip](#racer-pages)), or Unrated. The tooltip is the rating's explanation.
- **Comments:** each corner has a speech bubble, drawn after the page loads `comments.json`. A racer's first comment in a race is the one used. The draw follows these tiers, in order:
  1. **Both commented in a race they shared:** both bubbles come from one such race, so their attributions name the same mode and day.
  2. **Otherwise each corner is drawn on its own:** from a shared race where only that racer commented, else from any other race of theirs.
  - A comment over 140 characters is cut with "…", and hovering or tapping the bubble shows the whole comment. The bubble's attribution is the racer's place there, the mode and the relative day, linked to the race. A racer with no comment has no bubble.
  - **↻** (Other comments) draws again, avoiding the last pick while there is another. It shows only when there is more than one draw to choose from, and never replays the entrance.
- **Win chance:** one tall bar, the red corner's share in red and the blue's in blue, from the two racers' overall ratings with the formula of the head-to-head Win chance column (see [Ladder rating](../README.md#ladder-rating)). It is the chance of winning their next race together. Percentages are whole numbers that add to 100, the red one rounded and the blue one the rest, and a block prints its share only when it is at least 12%. If either racer is unrated it reads "Win chance needs both racers rated". It never uses mode or season ratings.
- **Head to head:** below the bar, the red corner's wins and the blue corner's wins against each other over all modes and all time, with the outcome rule of [Racer statistics](#racer-statistics). Between them are the draws when there are any, the races together and "last met" with that race's date, linked to it. The leading side's count is emphasised. A pair that has not met reads "Never raced each other".
- **Tale of the tape:** the two racers row by row, all modes, for the current season or all time. Under its title, a toggle in a group labelled "Comparison period" picks the period: the current season, pressed by default and labelled like "Season 11 · Panda" with the full name in its tooltip, then **All time**, as on the [Top-rated banner](#top-rated-banner). The choice holds across new pairs and swaps while Face Off is open, and a reload starts on the season again. With no seasons there is no toggle and the tape is all time. Under the toggle, the lead line is "X leads N–M" in rows won, or "All square at N–M". Each row shows the red value, the label and the blue value.
  - **Values:** a season's come from that season's leaderboard rows and its ratings; all time's from the all-time leaderboard rows (the board under **All seasons**, see [Racer leaderboard](#racer-leaderboard)) and the all-time overall rating.

  | Row | Value | Scored |
  |---|---|---|
  | Rating | the period's overall rating with its "#rank", and "provisional" below 5 finishes in the period | higher wins |
  | Races | races entered | not scored: it is activity, not form |
  | Wins | first places | more wins |
  | Podiums | diamond, gold, silver and bronze medals together, split in the tooltip | more wins |
  | Win % | races won outright, of races entered | higher wins |
  | H2H win % | head-to-head matchups won against the field | higher wins |
  | Finish % | races finished, of races entered | higher wins |
  | Head to head | their races together in the period, as each racer's wins in them, with draws in the tooltip | more wins |

  - **Bars:** the counts (Races, Wins, Podiums, Head to head) scale to the larger of the two values, and the rates (Win %, H2H win % and Finish %) to a 0-100% axis. Rating has no bar.
  - **Styling:** in a scored row the winner's side leads, the other trails, and a tie shows both as tied. An unscored row and a row with a missing value are plain. Hovering a value gives it with its meaning.
  - **Switching:** the toggle redraws only the rows and the lead line, so the pressed button keeps focus, and the new bars grow from the spine.
  - **Missing racers:** in a season, a racer with no race in it shows "No races" in the Rating row and dashes under it, and no row is scored. The lead line reads "Only X has raced this season", or "Neither has raced this season yet" with no rows at all.
- **Badges:** under the bands, each corner lists "Only X (n)", the badges that racer holds and the other lacks, rarest first by holders and then by when they were earned. Between the lists, a count of the badges both hold, "of N badges". A corner with none reads "Nothing Y lacks". When neither racer holds a badge the other lacks, the lists give way to "Same badges, every one". A tile is a badge as on a racer page, and opens its holders panel (see [Racer pages](#racer-pages)).
- **Motion:** a pair's entrance plays once each time a new pair is shown: the corners slide in, the VS stamps, the bands rise, the bar pulls from 50/50, the tape grows from the spine, the badges pop in turn and a sheen crosses the card. With reduced motion the card shows its final state at once, without the sheen.
- **Phones:** at 620px and below the corners stay face to face with smaller avatars, and each bubble moves under them, chat-style, with a coloured edge at the side of its corner. The empty card's prompt takes a full row, the "Most met" chips sit under the corners, the badge lists stack and nothing scrolls sideways. The nameplate's ▾ caret is hidden there.
- **Colours:** the corners use `--corner-a` (red) and `--corner-b` (blue), their own tokens that never read as a mode. They colour fills, rings and bars only, never text, and both themes keep 3:1 against the panel.

## Dialogs, keyboard and touch

- Escape closes an open tooltip or dialog. In a Face Off picker, the first Escape closes its option list and the next puts the picked name back. A modal dialog makes the rest of the page inert while it is open and gives focus back when it closes.
- Status toasts close by themselves after 3 seconds, or when clicked.
- **Tap:** on a touch screen, a tap shows the tooltip a mouse would show on hover, above what was tapped. A second tap on it, a touch anywhere else or a scroll closes it.
  - Links and buttons keep their own action on tap and show no tooltip. That covers a sort header's medal rule, a top-rated runner-up's rank and a badge's share, which show only on hover or focus.
  - A tag or comment inside a race row both opens the race and shows its tooltip.
- **Touch screens** (no hover, coarse pointer):
  - Tap targets are at least 24px: sort headers are 32px tall, a head-to-head table's VS link is 24px and a dialog's close button is 44px. A Face Off picker's options are 44px.
  - Scrolling inside a dialog doesn't carry on into the page behind it.
- **Narrow screens:** at 980px and below, strips and tables that scroll sideways show a soft shadow on the side where more is hidden. The badge-holders list in a dialog shows one at its bottom.
- At phone width the search boxes and the season select use 16px text, so iOS does not zoom the page when one is focused.

## Easter eggs

- **The code:** Up, Up, Down, Down, Left, Right, Left, Right, B, A, typed outside a text field, opens a dialog reading "It's dangerous to go alone! Take this." over an assembling Triforce.
- **Search words:** typing exactly `ganon`, `triforce` or `pedestal` into either search field, ignoring case and surrounding spaces, tints the top rail red with "Dun dun dun…", opens the Triforce dialog, or shows "You drew the Master Sword!".
- **Pieces of Heart:** four are hidden: the code, `pedestal`, a triple click on the footer credit, and the ✦ spark beside a palindrome finish time. Each new piece fills a quarter of a heart in the masthead, shown above the theme toggle once the first is found, and says "You found a Piece of Heart! (N/4)". The fourth makes it a Heart Container: "You got a Heart Container!". Pieces are kept in this browser's local storage.
- **The visitor counter** calls no service. It shows the ten-minute intervals since 2026-09-11 plus this browser's own page loads, as seven zero-padded digits. **View site stats** and a hidden footer link go to YouTube.
- With reduced motion the Triforce does not animate and the rail tint is static.

## Automatic updates

`site/version-check.js` checks `version.json` when the page loads, whenever the tab becomes visible again, and every five minutes. It reloads the page once if the published token no longer matches the one embedded in the loaded document (see [Versioning static assets](operations.md#versioning-static-assets)).

- A check on load or when the tab becomes visible reloads at once.
- A five-minute poll that finds a new version while the page is on screen waits instead, and reloads at the next visibility change, so the page never reloads under someone reading it. A tab that stays on screen the whole time therefore keeps the old version until it is hidden.
- The reload is guarded by `sessionStorage`: a tab reloads at most once per newly published version. Without that guard a browser still serving a cached `index.html` would report the old token forever and reload in a loop.
