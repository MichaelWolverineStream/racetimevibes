# Highlights

- **Purpose:** every race tag, badge, streak, preset statistic, record, fact and claim to fame, and the rule behind each.
- **Read before:** changing `scripts/highlights.py`. To add, remove or change a badge, follow the `badges` skill in [.claude/skills/badges/SKILL.md](../.claude/skills/badges/SKILL.md).
- **Code:** `scripts/highlights.py` (thresholds are constants at the top of the module), `scripts/badge_images.py`; badge art in `site/assets/badges/`.

`scripts/highlights.py` derives every tag, badge, streak, preset statistic, record, claim to fame and fact in one chronological pass, and `participants.json` publishes the results. Finishers, timed finishers, the winner and provisional racers are as defined under [Entrants](definitions.md#entrants); where a winner is anonymous, winner-dependent tags are skipped. Every tag and badge that reads a rating reads the overall rating.

## Race tags

Within a race, tags appear in the order mode record, preset record, first win, upset, photo, PB, preset PB, milestone, badge.

Each kind has its own colour, and so does the Hardest preset tag. The colour fills the tag's dot and outlines its pill. The colours stay distinct from each other under colour-blind simulation, and every tag names its kind, so colour is never the only cue.

The two record tags are the exception: they share the PB tag's green, because a record is a PB for the whole ladder, and they shine instead. Their pill has a green-tinted fill and a soft glow, their dot is a four-point sparkle, and a sheen sweeps across them every four seconds. With reduced motion the sheen stays still and the rest remains.

The Preset PB tag shares the PB tag's green too, without the shine, because it is a PB on a narrower scope. Its label tells the two apart.

- **Mode record:** the race's fastest time beats the mode's record in whole seconds, the precision the page shows. The first race in a mode sets the baseline and is not tagged.
- **Preset record:** the race's fastest time beats the preset's record in whole seconds, and the race does not set a mode record. A mode record always beats the preset's record too, so a race carries one record tag at most, the wider one. The first race in a preset sets the baseline and is not tagged. A race without a preset has no preset record.
- **First win:** the racer's first place-1 result in any mode.
- **Upset:** the winner's pre-race win chance against the favourite is 15% or less. The favourite is the highest pre-race overall rating among the entrants who are not provisional; the race needs at least 3 identified entrants, and the winner must not be the favourite.
- **Photo finish:** places 1 and 2 are both timed finishers separated by less than 5 seconds. The margin uses the exact times.
- **PB:** a strict improvement, in whole seconds, on the racer's previous best timed finish in the same mode. The first timed finish in a mode is the baseline and is not tagged.
- **Preset PB:** a strict improvement, in whole seconds, on the racer's previous best timed finish on the same preset, when the finish is not a PB. A PB always beats the racer's best on that preset too, so a finish carries one PB tag at most, the wider one, and a PB still becomes the racer's best on its preset. The first timed finish on a preset is the baseline and is not tagged. A race without a preset has no preset PB and leaves the racer's preset bests as they were.
- **Milestone:** the racer's entry count, in any status, reaches 25, 50, 100, 250, 500 or 1000.
- **Badge:** a badge newly unlocked in this race. First Blood is never tagged, because First win covers it.

## Badges

Each badge records the index of the race that unlocked it.

| Badge | Rule |
|---|---|
| First Blood | Win a race. |
| Hat Trick | Win 3 races in a row, in the racer's own sequence. |
| Hummers | Win 6 races in a row. |
| OkDudeLucksack | Win 9 races in a row. |
| Double D | Win 10 races. |
| Quarter Pounder | Win 25 races. |
| Full Pounder | Win 50 races. |
| On the Ped | Finish on the podium in 5 races in a row. |
| Ped Regular | Finish on the podium in 10 races in a row. |
| Bridesmaid | Finish 2nd in 3 races in a row. |
| No Dodge | Finish 10 races in a row. |
| More No Dodge | Finish 40 races in a row. |
| Nice | Finish 69 races in a row. |
| CenturianV | Finish 105 races. |
| BlazeIt | Finish 420 races. |
| >174 | Enter 175 races you don't win. |
| Omnivore | Finish a race in 7 different modes. |
| Apex Predator | Win a race in 7 different modes. |
| Connoisseur | Finish 20 different seed presets. |
| Rival | Finish ahead of the same opponent 20 times. |
| Sniper | Win by less than 5 seconds. |
| Sniped! | Finish 2nd, less than 5 seconds behind the winner. |
| RETIME | Finish less than 1 second from another racer. |
| Palindrome | Finish with a time whose H:MM:SS digits, hour unpadded and colons removed, read the same both ways. |
| Déjà Vu | Finish two races with exactly the same time. |
| God Route | Win by more than 10 minutes in a field of 7 or more while not its top-rated racer. |
| Big Routing | Win by more than 10 minutes in a field of 7 or more as its top-rated racer. |
| Upsetti | Finish 2nd, 10 minutes or more behind the winner, in a field of 7 or more as its top-rated racer. |
| BigBrain | Win by 15 minutes or more in a field of 9 or more. |
| Big Game Hunter | Win a race with 16 or more entrants, anonymous entrants included. |
| Glitched | Finish a glitched race. |
| Glitched! | Finish 10 glitched races. |
| G1!tch3d!!! | Finish 30 glitched races. |
| G%!#ch@d!#? | Finish 100 glitched races. |
| The Upside Down | Finish an inverted race. |
| Mirror Universe | Finish 30 inverted races. |
| Keys | Finish a keys race. |
| Locksmith | Finish 30 keys races. |
| Simpler Times | Finish 10 simple races. |
| Simpler Life | Finish 30 simple races. |
| Had Fun | Finish a fun race. |
| Kept Having Fun | Finish 10 fun races. |
| Jet Seed | Finish a Casual Boots race in under 70 minutes. |
| Jet Lag | Win a Casual Boots race in 100 minutes or more. |
| Driving Lesson | Finish a spoiler race. |
| Cruisin' | Win a spoiler race. |
| Entrance | Finish an entrance race. |
| Entranced | Enter an entrance race on 5 days in a row. |
| Entranced! | Enter an entrance race in 20 weeks in a row. |
| What? | Enter a mystery race. |
| I Like It Here | Finish 10 mystery races. |
| BUSTIN' | Finish 30 mystery races. |
| Both Ends | Finish a mystery race in under 30 minutes, and another in over 130 minutes. |
| Grocery Stick | Finish in the exact middle of an odd field of 5 or more entrants. |
| Lanterne Rouge | Finish last in a field of 10 or more where everyone finished. |
| Rando Sicko | Finish 5 races in one day. |
| Touched Grass | Enter a race after 30 or more days away, counting from 2026-08-01. |
| Yap | Comment on 11 races in a row. |
| Step Letter | Be in a race of 5 or more where everyone finished in alphabetical order. |
| Rettel Pets | Be in a race of 5 or more where everyone finished in reverse alphabetical order. |
| Badger | Collect 30 badges. |

A badge is either a counter, which unlocks when a count reaches its target, or a one-race badge, which unlocks on a single result. In the notes below, a **finish** is a finisher's result, and **day**, **week** and **field** are as defined in [definitions.md](definitions.md). Each badge ships an animated image and a still under `site/assets/badges/`; how the page shows them is under [Racer pages](dashboard.md#racer-pages).

- **Streak and count badges:** win and finish runs follow the racer's own race sequence across all modes, and a DNF or DQ ends a finish run. The one exception is a DNF in a field of two whose other racer finished: it neither ends the finish run nor counts toward it. A DNF still ends the run when both racers DNF or the other was DQ'd. CenturianV and BlazeIt count finishes. Double D, Quarter Pounder and Full Pounder count wins, in a row or not. On the Ped and Ped Regular use the podium run (places 1-3), which, unlike the finish run, has no DNF exception. >174 counts every race entered and not won, DNFs included. A mode's count badges count finishes in that mode.
- **Bridesmaid:** 2nd places in a row in the racer's own race sequence. Any other result ends the run, a DNF included, even in a field of two.
- **Connoisseur:** distinct seed presets finished. A preset is one row of the published preset list, so a name that two modes share, such as MMMM (Not Slow), counts twice.
- **Rival:** finishes ahead of one opponent, across all modes. Like an H2H win, it counts each opponent who placed lower or didn't finish. An equal place counts for neither racer.
- **Déjà Vu:** two finishes with the same time in whole seconds, the precision the page shows, in any modes. It unlocks on the second of them.
- **Lanterne Rouge:** named after the last-placed rider of the Tour de France. The field is at least 10, every entrant is identified and finished, as for Step Letter, and the finisher whose place equals the field earns it.
- **What?:** any entry in the mystery mode counts, DNFs included.
- **Yap:** the racer's own races in order, DNFs included, each with a comment that isn't blank, the same comments that `comments.json` publishes. A race without one ends the run. Only whether a comment exists reaches `highlights.py`; its text never enters `participants.json`.
- **Touched Grass:** the racer needs an earlier race, and the gap since it is counted only from 2026-08-01, so a break that started before then counts from that date and the earliest unlock is 2026-08-31. Any entry counts as the return, DNFs included.
- **Entranced and Entranced!:** any entry in the entrance mode counts, DNFs included, and several races on one day or in one week count once.
- **Rando Sicko:** finishes only, counted per day.
- **Sniper and Sniped!:** the photo tag's condition. Places 1 and 2 are both timed and the margin is under 5 seconds. Sniper goes to the winner and Sniped! to the runner-up.
- **RETIME:** two finishers' raw times, before rounding to whole seconds, are less than 1 second apart, at any places. Both earn it, and so does everyone in a chain of such gaps. Only identified, timed finishers count.
- **Jet Seed and Jet Lag:** the seed preset is named exactly Casual Boots.
- **Both Ends:** one mystery finish under 30 minutes and another over 130 minutes, in either order. It unlocks on the race that completes the pair.
- **Grocery Stick:** the field is odd and at least 5, and the finisher whose place is the middle one, `(field + 1) / 2`, earns it.
- **Step Letter:** the field is at least 5, every entrant is identified and finished, and the names in place order never decrease when compared without case. Every entrant earns it.
- **Rettel Pets:** Step Letter's rule with the names in place order never increasing.
- **Names for Step Letter and Rettel Pets:** each entrant's name as archived with that race, normally within hours of it, not their current name, so a later rename never adds or removes either badge. The archive was first filled in bulk on 2026-10-27, so earlier races carry the names racers had on that date.
- **God Route:** places 1 and 2 are both timed and more than 10 minutes apart, using the exact times, and the field is at least 7. "Top-rated" is the entrant with the best pre-race overall rating among those who are not provisional and raced in the previous 90 days. When no entrant qualifies, the winner earns it.
- **Big Routing:** God Route's condition, but the winner must be the top-rated entrant, as defined for God Route. When no entrant qualifies, nobody earns it. Every such win earns exactly one of God Route and Big Routing.
- **Upsetti:** places 1 and 2 are both timed and at least 10 minutes apart, using the exact times, the field is at least 7, and place 2 is the top-rated entrant, as defined for God Route. When no entrant qualifies, or the top-rated entrant finishes elsewhere or doesn't finish, nobody earns it. Unlike God Route's "more than", exactly 10 minutes is enough.
- **BigBrain:** places 1 and 2 are both timed and at least 15 minutes apart, using the exact times, and the field is at least 9. Ratings play no part.
- **Omnivore:** needs 7 of the 8 modes, because hardly anyone finishes all 8.
- **Apex Predator:** Omnivore's rule for wins: a win in 7 different modes.
- **Badger:** counts every badge the racer holds, First Blood included. It unlocks in the same race as the 30th badge.

## Streaks

- **Daily:** consecutive Eastern Time dates with at least one entry. The current streak is the run ending on the racer's last race day.
- **Win, podium and finish:** runs over the racer's own race sequence across all modes. Podium means places 1-3; finish means finisher. A finish run follows the DNF exception under [Streak and count badges](#badges). The current streak is the run still open after the racer's latest race, and is absent if that race broke it.

## Presets

Presets are named as defined under [Races](definitions.md#races). Statistics are races, entries, DNF rate (entries that are not finishers), median time, median winning time, mean field size, and the fastest finish.

- **Hardest preset:** the highest DNF rate among presets with 8 or more races.

## Records

- **Mode records:** one progression step per race that sets a new record, per mode.
- **Photo finishes:** the 10 smallest margins between places 1 and 2 among photo finishes (see [Race tags](#race-tags)).
- **Blowouts:** the 10 largest margins between places 1 and 2, in fields of 5 or more racers.
- **Big fields:** the 10 largest fields of 16 or more that had an identified winner.
- **Most races:** the 10 racers with the most entries.
- **Most-met rivals:** the 10 pairs of racers who entered the most races together. Any entry counts, DNFs and DQs included.
- **Streak boards:** the 10 best streaks of each kind. Whether a streak is still active depends on today's date, so the page works that out (see [Hall of records](dashboard.md#hall-of-records)).
- **Facts:** English sentences generated from the data, such as the hardest preset, the biggest upset or the busiest day of the week. A fact without enough data is skipped.
  - Each fact is published as three strings: the text before its headline number, the number, and the text after. The page highlights exactly that number, even when a racer's name holds digits.
  - A streak fact needs a streak of 2 or more, and the comeback fact a break of 30 days or more, measured as for Touched Grass but with no start date.
  - Mode facts leave out the `unknown` mode.
  - Every fact comes from the archive alone. A record's age counts to the archive's latest race date, not to today.

## Claim to fame

A racer's claim to fame is the one thing about them that the fewest racers match. The [Featured daily racer](dashboard.md#featured-daily-racer) card shows it.

- **Stats:** wins, longest win streak, longest podium streak, longest finish streak, races entered, longest daily streak, PBs, different opponents, night finishes and longest comeback, in that order.
  - PBs count improvements, as the PB tag does, so a mode's first timed finish is not one. Preset PBs don't count.
  - Different opponents counts every racer met in a race, DNFs and DQs included, as Most-met rivals does.
  - Night finishes are finishes in races starting between midnight and 5 AM ET.
  - The longest comeback is the most days between two of the racer's consecutive race days.
- **Count:** a stat takes part only at 2 or more. Its count is the number of racers whose value is at least as high, the racer included, so tied racers share a count.
- **Badge:** the racer's rarest badge takes part too, with its holder count as the count. Among equally rare badges, the one earlier in the badge table wins.
- **Pick:** the lowest count wins. Equal counts go to the stat earlier in the list above, and a badge comes after every stat. A racer has no claim when the lowest count is above 10% of the racers who have entered a race.
