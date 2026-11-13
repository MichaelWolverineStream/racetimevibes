// Face Off: two racers on one mirrored versus card, the red corner (a) on the left and the blue corner (b)
// on the right. Everything is derived on the page from participants.json and comments.json; the rules are
// in docs/dashboard.md › Face Off. leaderboard.js owns the route and calls show() and hide().
const FaceOff = (() => {
  const SIDES = ["a", "b"];
  const CORNER_NAMES = { a: "Red corner", b: "Blue corner" };
  // The largest avatar; faceoff.css sets the shown size per screen width through --faceoff-avatar.
  const AVATAR_SIZE = 128;
  const OPTION_AVATAR_SIZE = 24;
  const CHIP_AVATAR_SIZE = 20;
  const MAX_OPTIONS = 8;
  const MAX_SUGGESTIONS = 5;
  // Keeps a corner's bubble to about three lines; the whole comment shows on hover or tap.
  const BUBBLE_MAX_LENGTH = 140;
  // The win-chance bar prints a share inside its block only when the block has room for it.
  const ODDS_LABEL_MIN_SHARE = 0.12;
  // Random rivalry picks among the pairs who have raced each other most.
  const RIVALRY_POOL = 25;
  // The entrance plays once per new pair; after this the card drops the class, so a redrawn comment
  // never replays it. It must outlast the longest delay plus duration in faceoff.css.
  const ENTER_MS = 2600;
  // Tale-of-the-tape rows, top to bottom, the same for the current season and for all time; the period toggle
  // names the scope. better: 1 when higher wins the row, 0 when the row is not scored. bar: "count" scales to
  // the larger of the two, "rate" is a 0-100% axis, null is no bar.
  const TAPE_ROWS = [
    { key: "rating", label: "Rating", better: 1, bar: null, tip: "Ladder points, all modes · higher wins the row" },
    { key: "races", label: "Races", better: 0, bar: "count", tip: "Races entered · not scored: it is activity, not form" },
    { key: "wins", label: "Wins", better: 1, bar: "count", tip: "First places · more wins the row" },
    { key: "podiums", label: "Podiums", better: 1, bar: "count", tip: "Diamond, gold, silver and bronze medals · more wins the row" },
    { key: "winRate", label: "Win %", better: 1, bar: "rate", tip: "Races won outright, of races entered · higher wins the row" },
    { key: "h2hRate", label: "H2H win %", better: 1, bar: "rate", tip: "Head-to-head matchups won against the whole field · higher wins the row" },
    { key: "finishRate", label: "Finish %", better: 1, bar: "rate", tip: "Races finished, of races entered · higher wins the row" },
    { key: "meetings", label: "Head to head", better: 1, bar: "count", tip: "Their races together · more wins takes the row" },
  ];

  let card = null;
  let parts = null;
  let pickers = null;
  let shown = { a: null, b: null };
  let visible = false;
  // Bumped by every show and hide, so a comment draw that resolves late never paints a stale pair.
  let token = 0;
  let enterTimer = null;
  let commentPool = null;
  let lastDraw = null;
  let rivalries = null;
  // The corner whose avatar link takes focus once the pair renders, set by the buttons that a render removes
  // (a Most met chip, Random rivalry), so keyboard focus never falls back to the page.
  let focusCorner = null;
  // The tale of the tape's period: the current season (false) or all time (true). It holds across pairs and
  // swaps while the page is open, and a reload starts on the season again, like the top-rated toggle.
  let tapeAllTime = false;

  function element(tag, className, text = null) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function button(className, text, label, onClick) {
    const control = element("button", className, text);
    control.type = "button";
    control.setAttribute("aria-label", label);
    bindTooltip(control, label);
    control.addEventListener("click", onClick);
    return control;
  }

  function other(side) {
    return side === "a" ? "b" : "a";
  }

  function player(index) {
    return participants.players[index];
  }

  function entryOf(race, playerIndex) {
    return race.e.find((entry) => entry[0] === playerIndex);
  }

  function placeText(entry) {
    if (isFinisher(entry)) {
      return ordinal(entry[1]);
    }
    return entry[2] === DQ_STATUS ? "DQ" : "DNF";
  }

  function plural(count, word) {
    return `${integerFormatter.format(count)} ${word}${count === 1 ? "" : "s"}`;
  }

  // An avatar sized by --faceoff-avatar instead of UI.avatar's fixed pixels, monogram letter included.
  function bigAvatar(playerIndex) {
    const node = UI.avatar(playerIndex, AVATAR_SIZE);
    node.style.width = "var(--faceoff-avatar)";
    node.style.height = "var(--faceoff-avatar)";
    const monogram = node.querySelector(".ui-avatar__monogram");
    if (monogram) {
      monogram.style.fontSize = "calc(var(--faceoff-avatar) * 0.55)";
    }
    return node;
  }

  // ---- Route ----

  // Every pick goes through the hash, so the router stays the one path that renders. replace() adds no
  // history entry, so Back leaves Face Off rather than stepping back through picks.
  function navigate(pair) {
    const id = (index) => (index === null ? null : player(index).id);
    window.location.replace(faceoffHash(id(pair.a), id(pair.b)));
  }

  function pick(side, playerIndex) {
    const next = { ...shown, [side]: playerIndex };
    if (next[other(side)] === playerIndex) {
      next[other(side)] = null;
    }
    navigate(next);
  }

  // ---- Pickers: each corner's nameplate is an ARIA 1.2 combobox ----

  function candidates(side, query) {
    const taken = shown[other(side)];
    if (!query) {
      return Following.ids()
        .map((id) => playerIndexFor(id))
        .filter((index) => index !== undefined && index !== taken)
        .slice(0, MAX_OPTIONS);
    }
    const found = [];
    participants.players.forEach((racer, index) => {
      if (index !== taken && matchesQuery(racer.name, query)) {
        found.push(index);
      }
    });
    // Names that start with the query come first, then everything else in name order.
    const starts = (index) => (player(index).name.toLowerCase().startsWith(query) ? 0 : 1);
    found.sort((left, right) => starts(left) - starts(right) || player(left).name.localeCompare(player(right).name));
    return found.slice(0, MAX_OPTIONS);
  }

  function createPicker(side) {
    const root = element("div", `faceoff-picker faceoff-${side}`);
    root.style.gridArea = `pick-${side}`;
    const inputId = `faceoff-input-${side}`;
    const listId = `faceoff-list-${side}`;
    const label = element("label", "visually-hidden", `${CORNER_NAMES[side]} racer`);
    label.htmlFor = inputId;
    const field = element("div", "faceoff-picker__field");
    const input = element("input", "faceoff-picker__input");
    input.id = inputId;
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "Pick a racer";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);
    const caret = element("span", "faceoff-picker__caret", "▾");
    caret.setAttribute("aria-hidden", "true");
    field.append(input, caret);
    const list = element("ul", "faceoff-picker__list");
    list.id = listId;
    list.hidden = true;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", `${CORNER_NAMES[side]} racers`);
    root.append(label, field, list);

    let options = [];
    let active = -1;

    function pickedName() {
      return shown[side] === null ? "" : player(shown[side]).name;
    }

    function close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    }

    // Back to the picked racer's name, or empty, with the list closed.
    function restore() {
      input.value = pickedName();
      close();
    }

    function setActive(next) {
      const items = list.children;
      if (active >= 0 && items[active]) {
        items[active].setAttribute("aria-selected", "false");
      }
      active = next;
      if (active < 0) {
        input.removeAttribute("aria-activedescendant");
        return;
      }
      items[active].setAttribute("aria-selected", "true");
      input.setAttribute("aria-activedescendant", items[active].id);
      items[active].scrollIntoView({ block: "nearest" });
    }

    function choose(playerIndex) {
      input.value = player(playerIndex).name;
      close();
      // The same racer again leaves the hash as it is, which fires nothing: the input already shows them.
      if (shown[side] !== playerIndex) {
        pick(side, playerIndex);
      }
    }

    // The typed text as a query; the picked name still standing in the input asks for nothing yet.
    function query() {
      const text = input.value.trim();
      return text === pickedName() ? "" : text.toLowerCase();
    }

    function open() {
      options = candidates(side, query());
      list.replaceChildren();
      active = -1;
      input.removeAttribute("aria-activedescendant");
      if (!options.length) {
        close();
        return;
      }
      options.forEach((playerIndex, position) => {
        const option = element("li", "faceoff-picker__option");
        option.id = `faceoff-option-${side}-${position}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        option.append(UI.avatar(playerIndex, OPTION_AVATAR_SIZE), element("span", "faceoff-picker__name", player(playerIndex).name));
        // mousedown keeps focus in the input, so choosing never blurs it first.
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => choose(playerIndex));
        list.appendChild(option);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    input.addEventListener("focus", () => {
      input.select();
      open();
    });
    input.addEventListener("input", open);
    input.addEventListener("blur", restore);
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (list.hidden) {
          open();
        }
        if (!options.length) {
          return;
        }
        // From no highlight, ↓ starts at the first option and ↑ at the last; after that both wrap.
        const step = event.key === "ArrowDown" ? 1 : -1;
        const start = step === 1 ? 0 : options.length - 1;
        setActive(active < 0 ? start : (active + step + options.length) % options.length);
      } else if (event.key === "Enter") {
        // A typed query with a single match picks it; the followed racers listed for an empty query never
        // get picked without a highlight.
        const only = options.length === 1 && query() !== "" ? options[0] : null;
        const chosen = active >= 0 ? options[active] : only;
        if (!list.hidden && chosen !== null && chosen !== undefined) {
          event.preventDefault();
          choose(chosen);
        }
      } else if (event.key === "Escape") {
        // The first Escape closes the list; the next one puts the picked name back.
        if (!list.hidden) {
          event.preventDefault();
          close();
        } else {
          restore();
        }
      }
    });

    // A route change shows its pair in the input, unless someone is typing there. An empty corner facing a
    // picked racer asks for a challenger.
    function sync() {
      input.placeholder = shown[side] === null && shown[other(side)] !== null ? "Pick a challenger" : "Pick a racer";
      if (document.activeElement !== input) {
        restore();
      }
    }

    return { root, sync, close };
  }

  // ---- Skeleton: the hero grid is built once, so a picker keeps focus while its pair changes ----

  function build() {
    if (card) {
      return;
    }
    card = document.querySelector("#faceoff-card");
    const hero = element("section", "faceoff-hero");
    hero.setAttribute("aria-label", "The corners");
    parts = { hero };
    pickers = {};
    // Grid areas place the pieces; the DOM keeps each corner together, so the tab order runs through
    // the red corner, then the spine, then the blue corner.
    const corner = {};
    SIDES.forEach((side) => {
      ["say", "frame", "rate", "chips"].forEach((slot) => {
        const node = element("div", `faceoff-${slot} faceoff-${slot}--${side} faceoff-${side}`);
        node.style.gridArea = `${slot}-${side}`;
        parts[`${slot}-${side}`] = node;
      });
      pickers[side] = createPicker(side);
      corner[side] = [parts[`say-${side}`], parts[`frame-${side}`], pickers[side].root, parts[`rate-${side}`], parts[`chips-${side}`]];
    });
    // The spine holds the VS and the corner actions level with the avatars. The note under it (the empty
    // card's Random rivalry) is its own grid item, so on a phone it can take a full row instead of widening
    // the middle column.
    const spine = element("div", "faceoff-spine");
    spine.style.gridArea = "spine";
    // Two letters, so the gap between them can sit at the VS's middle, where the slash crosses.
    const vs = element("span", "faceoff-vs");
    vs.append(element("span", null, "V"), element("span", null, "S"));
    vs.setAttribute("aria-hidden", "true");
    parts.spine = spine;
    parts.vs = vs;
    // Built once and only shown or hidden, so a focused button keeps focus while the pair it changed renders.
    parts.swap = button("faceoff-action", "⇄", "Swap corners", () => navigate({ a: shown.b, b: shown.a }));
    parts.again = button("faceoff-action faceoff-action--again", "↻", "Other comments", paintComments);
    parts.swap.hidden = true;
    parts.again.hidden = true;
    const actions = element("div", "faceoff-spine__actions");
    actions.append(parts.swap, parts.again);
    spine.append(vs, actions);
    parts.note = element("div", "faceoff-note");
    parts.note.style.gridArea = "note";
    hero.append(...corner.a, spine, parts.note, ...corner.b);
    parts.body = element("div", "faceoff-body");
    card.append(hero, parts.body);
    // A bubble, a chip row, a button or a font load can move the VS only by resizing the hero or one of
    // its pieces, so watching those keeps the slash on the VS.
    const observer = new ResizeObserver(pivotOnVs);
    [hero, ...hero.children].forEach((node) => observer.observe(node));
  }

  // The gold slash and the colour split pivot on the VS's middle, wherever the rows above have put it, so the
  // slash always runs between the V and the S. Offsets ignore transforms, so the entrance cannot skew this.
  // The property moves only paint (faceoff.css), never layout, so it cannot set the observer off again.
  function pivotOnVs() {
    const { hero, spine, vs } = parts;
    const middle = spine.offsetTop + vs.offsetTop + vs.offsetHeight / 2;
    hero.style.setProperty("--split-shift", `${middle - hero.clientHeight / 2}px`);
  }

  // ---- Hero ----

  function renderFrame(side) {
    const host = parts[`frame-${side}`];
    const index = shown[side];
    if (index === null) {
      const silhouette = element("span", "faceoff-silhouette", "?");
      silhouette.setAttribute("aria-hidden", "true");
      host.replaceChildren(silhouette);
      return;
    }
    const link = element("a", "faceoff-frame__link");
    link.href = playerHash(player(index).id, ALL_MODES);
    link.setAttribute("aria-label", `${player(index).name}'s racer page`);
    link.append(bigAvatar(index));
    host.replaceChildren(link);
  }

  function renderRate(side) {
    const host = parts[`rate-${side}`];
    const index = shown[side];
    host.replaceChildren();
    if (index === null) {
      return;
    }
    // A fresh line each time, because the host stays and a tooltip binds for good.
    const line = element("p", "faceoff-rate__line");
    const rating = ratingFor(player(index));
    if (rating === null) {
      line.append(element("span", "faceoff-rate__points", "Unrated"));
    } else {
      const rank = ratingRank(index, ALL_MODES);
      line.append(element("strong", "faceoff-rate__points", String(rating.rating)));
      if (rank !== undefined) {
        line.append(element("span", "faceoff-rate__rank", `#${integerFormatter.format(rank)} of ${integerFormatter.format(ratingRanks(ALL_MODES).size)}`));
      }
      if (rating.finishes < participants.rating.provisional_finishes) {
        line.append(element("span", "faceoff-rate__note", "provisional"));
      }
    }
    bindTooltip(line, `All-time rating · ${ratingTooltip(ALL_MODES)}`);
    host.append(line);
  }

  // With one corner picked, the empty one suggests the racers the picked one has met most.
  function renderChips(side) {
    const host = parts[`chips-${side}`];
    host.replaceChildren();
    const rival = shown[other(side)];
    if (shown[side] !== null || rival === null) {
      return;
    }
    const opponents = computeHeadToHead(rival, ALL_MODES)
      .sort((left, right) => right.meetings - left.meetings || player(left.opponent).name.localeCompare(player(right.opponent).name))
      .slice(0, MAX_SUGGESTIONS);
    if (!opponents.length) {
      return;
    }
    host.append(element("span", "faceoff-chips__label", "Most met"));
    opponents.forEach((record) => {
      const chip = element("button", "faceoff-chip");
      chip.type = "button";
      chip.append(UI.avatar(record.opponent, CHIP_AVATAR_SIZE), element("span", null, player(record.opponent).name));
      chip.setAttribute("aria-label", `Face off with ${player(record.opponent).name}, ${plural(record.meetings, "race")} together`);
      bindTooltip(chip, `${plural(record.meetings, "race")} together`);
      chip.addEventListener("click", () => {
        focusCorner = side;
        pick(side, record.opponent);
      });
      host.append(chip);
    });
  }

  function renderSpine() {
    parts.swap.hidden = shown.a === null && shown.b === null;
    parts.again.hidden = true;
    parts.note.replaceChildren();
    if (shown.a === null && shown.b === null) {
      parts.note.append(button("faceoff-rivalry", "Random rivalry", "Random rivalry: two racers who have met often", randomRivalry));
    }
  }

  // The pairs who have raced each other most, counted once on first use.
  function topRivalries() {
    if (!rivalries) {
      const meetings = new Map();
      participants.races.forEach((race) => {
        const field = race.e.map((entry) => entry[0]).sort((left, right) => left - right);
        field.forEach((first, position) => {
          field.slice(position + 1).forEach((second) => {
            const key = `${first}|${second}`;
            meetings.set(key, (meetings.get(key) ?? 0) + 1);
          });
        });
      });
      rivalries = [...meetings]
        .sort((left, right) => right[1] - left[1])
        .slice(0, RIVALRY_POOL)
        .map(([key]) => key.split("|").map(Number));
    }
    return rivalries;
  }

  function randomRivalry() {
    const pool = topRivalries();
    if (!pool.length) {
      return;
    }
    const [first, second] = pool[Math.floor(Math.random() * pool.length)];
    const flip = Math.random() < 0.5;
    focusCorner = "a";
    navigate({ a: flip ? second : first, b: flip ? first : second });
  }

  // ---- Shared history ----

  // Every race the two entered together, oldest first, and the red corner's record in them: all time,
  // and in the current season. Outcomes use compareEntries, so they match the racer page's head-to-head.
  function pairFacts(a, b) {
    const racesOfB = new Set(raceIndexesByPlayer[b]);
    const shared = raceIndexesByPlayer[a].filter((raceIndex) => racesOfB.has(raceIndex));
    const season = currentSeason();
    const tally = () => ({ wins: 0, losses: 0, draws: 0, meetings: 0 });
    const allTime = tally();
    const thisSeason = tally();
    const count = (record, outcome) => {
      record.meetings += 1;
      if (outcome > 0) {
        record.wins += 1;
      } else if (outcome < 0) {
        record.losses += 1;
      } else {
        record.draws += 1;
      }
    };
    shared.forEach((raceIndex) => {
      const race = participants.races[raceIndex];
      const outcome = compareEntries(entryOf(race, a), entryOf(race, b));
      count(allTime, outcome);
      if (season !== null && race.s === season) {
        count(thisSeason, outcome);
      }
    });
    return { shared, allTime, thisSeason, season };
  }

  // ---- Band: win chance and record ----

  function oddsBand(a, b, facts) {
    const band = element("section", "faceoff-band faceoff-odds");
    band.setAttribute("aria-label", "Win chance and head to head");
    const chance = winChance(player(a), player(b));
    if (chance === null) {
      band.append(element("p", "faceoff-odds__none", "Win chance needs both racers rated"));
    } else {
      const legend = element("div", "faceoff-odds__legend");
      legend.setAttribute("aria-hidden", "true");
      legend.append(
        element("span", "faceoff-odds__key faceoff-odds__key--a", player(a).name),
        element("span", "faceoff-odds__key faceoff-odds__key--b", player(b).name),
      );
      const bar = element("div", "faceoff-odds__bar");
      bar.setAttribute("role", "img");
      // Whole percentages that always add up to 100, the red corner's rounded and the blue's the rest.
      const percentA = Math.round(chance * 100);
      bar.setAttribute("aria-label", `Win chance in their next race together: ${player(a).name} ${percentA}%, ${player(b).name} ${100 - percentA}%`);
      [["a", chance, percentA], ["b", 1 - chance, 100 - percentA]].forEach(([side, share, percent]) => {
        const block = element("span", `faceoff-odds__part faceoff-odds__part--${side}`);
        block.style.setProperty("--share", String(share));
        if (share >= ODDS_LABEL_MIN_SHARE) {
          block.append(element("span", "faceoff-odds__share", `${percent}%`));
        }
        bindTooltip(block, `${player(side === "a" ? a : b).name} · ${percent}% win chance`);
        bar.append(block);
      });
      const caption = element("p", "faceoff-odds__caption", "Win chance in their next race · from overall ratings");
      bindTooltip(caption, "From current overall ratings, as everywhere on the site. Mode and season ratings are standings only.");
      band.append(legend, bar, caption);
    }
    band.append(recordRow(facts));
    return band;
  }

  function recordRow(facts) {
    const row = element("div", "faceoff-record");
    const record = facts.allTime;
    const spine = element("div", "faceoff-record__spine");
    spine.append(element("span", "faceoff-record__label", "Head to head"));
    if (!record.meetings) {
      spine.append(element("span", "faceoff-record__detail", "Never raced each other"));
      row.append(element("span", "faceoff-record__wins faceoff-record__wins--a"), spine, element("span", "faceoff-record__wins faceoff-record__wins--b"));
      return row;
    }
    const detail = element("span", "faceoff-record__detail");
    const bits = [plural(record.meetings, "race") + " together"];
    if (record.draws) {
      bits.unshift(plural(record.draws, "draw"));
    }
    detail.append(`${bits.join(" · ")} · last met `);
    const last = participants.races[facts.shared.at(-1)];
    const link = element("a", null, formatRaceDate(last.d));
    link.href = `${RACE_ROOT}${last.r}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    detail.append(link);
    spine.append(detail);
    const lead = Math.sign(record.wins - record.losses);
    const wins = (side, count, leading) => {
      const node = element("strong", `faceoff-record__wins faceoff-record__wins--${side}`, integerFormatter.format(count));
      node.dataset.lead = leading;
      return node;
    };
    row.append(wins("a", record.wins, lead > 0 ? "yes" : lead < 0 ? "no" : "tie"), spine, wins("b", record.losses, lead < 0 ? "yes" : lead > 0 ? "no" : "tie"));
    row.setAttribute("aria-label", `Head to head, all modes and all time: ${player(shown.a).name} ${record.wins}, ${player(shown.b).name} ${record.losses}`);
    return row;
  }

  // ---- Band: the tale of the tape, for the current season or all time ----

  // One value in `season`, or all time when it is null. `row` is the racer's leaderboard row in that scope.
  function tapeValue(key, playerIndex, row, facts, side, season) {
    if (key === "meetings") {
      const record = season === null ? facts.allTime : facts.thisSeason;
      const draws = record.draws ? ` · ${plural(record.draws, "draw")}` : "";
      return { value: side === "a" ? record.wins : record.losses, tip: `wins in ${plural(record.meetings, "race")} together${draws}` };
    }
    // A racer with no race this season says so once, in the top row, and shows dashes under it.
    if (!row) {
      return key === "rating" ? { value: null, text: "No races" } : { value: null };
    }
    switch (key) {
      case "rating": {
        const rating = ratingFor(player(playerIndex), ALL_MODES, season);
        if (rating === null) {
          return { value: null };
        }
        const rank = ratingRank(playerIndex, ALL_MODES, season);
        const notes = [];
        if (rank !== undefined) {
          notes.push(`#${integerFormatter.format(rank)}`);
        }
        if (rating.finishes < participants.rating.provisional_finishes) {
          notes.push("provisional");
        }
        return { value: rating.rating, text: String(rating.rating), note: notes.join(" · ") };
      }
      case "races":
        return { value: row.races };
      case "wins":
        return { value: row.wins };
      case "podiums": {
        const total = row.diamond + row.gold + row.silver + row.bronze;
        return { value: total, tip: `${row.diamond} diamond · ${row.gold} gold · ${row.silver} silver · ${row.bronze} bronze` };
      }
      case "winRate":
        return { value: row.raceWinRate, text: formatRate(row.raceWinRate) };
      case "h2hRate":
        return { value: row.headToHeadRate, text: formatRate(row.headToHeadRate) };
      case "finishRate": {
        const share = (row.races - row.unfinished) / row.races;
        return { value: share, text: formatRate(share) };
      }
      default:
        throw new Error(`Unknown tape row ${key}`);
    }
  }

  // "a", "b", "tie", or null when the row is unscored or a side has no value.
  function rowWinner(spec, valueA, valueB) {
    if (!spec.better || valueA === null || valueB === null) {
      return null;
    }
    const difference = (valueA - valueB) * spec.better;
    return difference > 0 ? "a" : difference < 0 ? "b" : "tie";
  }

  function tapeSide(side, spec, cell, winner, scale, name) {
    const node = element("div", `faceoff-tape__side faceoff-tape__side--${side}`);
    node.dataset.state = winner === null ? "plain" : winner === "tie" ? "tie" : winner === side ? "lead" : "trail";
    const value = element("span", "faceoff-tape__value", cell.text ?? (cell.value === null ? "–" : integerFormatter.format(cell.value)));
    if (cell.note) {
      value.append(element("span", "faceoff-tape__note", cell.note));
    }
    node.append(value);
    if (spec.bar) {
      const track = element("span", "faceoff-tape__track");
      const fill = element("span", "faceoff-tape__fill");
      const length = cell.value === null ? 0 : spec.bar === "rate" ? cell.value : scale ? cell.value / scale : 0;
      fill.style.setProperty("--length", String(Math.max(0, Math.min(1, length))));
      track.append(fill);
      node.append(track);
    }
    const shownValue = cell.value === null ? "none" : value.firstChild.textContent;
    bindTooltip(node, cell.tip ? `${name} · ${spec.label}: ${shownValue} (${cell.tip})` : `${name} · ${spec.label}: ${shownValue}`);
    return node;
  }

  // The current season, then all time, built like the top-rated banner's period toggle (top-rated.js).
  function periodToggle(season, onSelect) {
    const toggle = element("div", "mode-toggle faceoff-tape__period");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "Comparison period");
    const choices = [
      { label: seasonLabel(season), allTime: false, full: seasonFullLabel(season) },
      { label: "All time", allTime: true, full: null },
    ];
    const buttons = choices.map((choice) => {
      const control = element("button", "mode-toggle__button", choice.label);
      control.type = "button";
      if (choice.full && choice.full !== choice.label) {
        bindTooltip(control, choice.full);
      }
      control.setAttribute("aria-pressed", String(choice.allTime === tapeAllTime));
      control.addEventListener("click", () => {
        if (choice.allTime === tapeAllTime) {
          return;
        }
        buttons.forEach((other, index) => other.setAttribute("aria-pressed", String(choices[index].allTime === choice.allTime)));
        onSelect(choice.allTime);
      });
      return control;
    });
    toggle.append(...buttons);
    return toggle;
  }

  // The band, its header and the toggle are built once per pair. Switching the period redraws only the rows
  // and the lead line, so the pressed button keeps focus, and the new bars grow from the spine.
  function tapeBand(a, b, facts) {
    const band = element("section", "faceoff-band faceoff-tape");
    const header = element("header", "faceoff-tape__header");
    const title = element("h3", "faceoff-band__title", "Tale of the tape");
    title.id = "faceoff-tape-title";
    band.setAttribute("aria-labelledby", title.id);
    const lead = element("p", "faceoff-tape__lead");
    bindTooltip(lead, "Rows won in the tape. Races are shown but not scored.");
    const list = element("div", "faceoff-tape__rows");
    list.setAttribute("role", "list");
    header.append(title);
    // With no seasons there is nothing to choose between, and the tape is all time.
    if (facts.season !== null) {
      header.append(periodToggle(facts.season, (allTime) => {
        tapeAllTime = allTime;
        fillTape(a, b, facts, list, lead, true);
      }));
    }
    header.append(lead);
    band.append(header, list);
    fillTape(a, b, facts, list, lead, false);
    return band;
  }

  function fillTape(a, b, facts, list, lead, grow) {
    const season = tapeAllTime ? null : facts.season;
    const rows = leaderboardRows(ALL_MODES, season);
    const rowOf = (playerIndex) => rows.find((row) => row.p === playerIndex) ?? null;
    const boardRows = { a: rowOf(a), b: rowOf(b) };
    const names = { a: player(a).name, b: player(b).name };
    list.replaceChildren();
    list.classList.toggle("faceoff-tape__rows--grow", grow);
    delete lead.dataset.leader;

    const missing = SIDES.filter((side) => !boardRows[side]);
    if (missing.length === 2) {
      lead.textContent = "Neither has raced this season yet";
      return;
    }

    const score = { a: 0, b: 0 };
    TAPE_ROWS.forEach((spec, position) => {
      const cells = {
        a: tapeValue(spec.key, a, boardRows.a, facts, "a", season),
        b: tapeValue(spec.key, b, boardRows.b, facts, "b", season),
      };
      const winner = missing.length ? null : rowWinner(spec, cells.a.value, cells.b.value);
      if (winner === "a" || winner === "b") {
        score[winner] += 1;
      }
      const scale = Math.max(cells.a.value ?? 0, cells.b.value ?? 0);
      const row = element("div", "faceoff-tape__row");
      row.setAttribute("role", "listitem");
      row.style.setProperty("--row", String(position));
      const label = element("span", "faceoff-tape__label", spec.label);
      bindTooltip(label, spec.tip);
      row.append(tapeSide("a", spec, cells.a, winner, scale, names.a), label, tapeSide("b", spec, cells.b, winner, scale, names.b));
      list.append(row);
    });

    if (missing.length) {
      const present = other(missing[0]);
      lead.textContent = `Only ${names[present]} has raced this season`;
    } else if (score.a === score.b) {
      lead.textContent = `All square at ${score.a}–${score.b}`;
    } else {
      const leader = score.a > score.b ? "a" : "b";
      lead.textContent = `${names[leader]} leads ${Math.max(score.a, score.b)}–${Math.min(score.a, score.b)}`;
      lead.dataset.leader = leader;
    }
  }

  // ---- Band: the badges only one of them holds ----

  function badgesBand(a, b) {
    const band = element("section", "faceoff-band faceoff-badges");
    band.setAttribute("aria-label", "Badges only one holds");
    const definitions = new Map(participants.badges.map((definition) => [definition.id, definition]));
    const held = { a: player(a).badges, b: player(b).badges };
    const only = (side) => Object.keys(held[side])
      .filter((id) => !(id in held[other(side)]) && definitions.has(id))
      .sort((left, right) => (badgeHolderCounts.get(left) ?? 0) - (badgeHolderCounts.get(right) ?? 0) || held[side][left] - held[side][right]);
    const lists = { a: only("a"), b: only("b") };
    const both = Object.keys(held.a).filter((id) => id in held.b).length;
    const total = participants.badges.length;

    const spine = element("div", "faceoff-badges__spine");
    spine.append(element("strong", "faceoff-badges__both", integerFormatter.format(both)), element("span", "faceoff-badges__caption", `both hold, of ${total} badges`));
    if (!lists.a.length && !lists.b.length) {
      spine.append(element("span", "faceoff-badges__same", "Same badges, every one"));
      band.append(spine);
      return band;
    }
    let order = 0;
    const column = (side) => {
      const node = element("div", `faceoff-badges__col faceoff-badges__col--${side}`);
      const heading = element("h3", "faceoff-badges__title");
      heading.append(`Only ${player(side === "a" ? a : b).name}`, element("span", "faceoff-badges__count", integerFormatter.format(lists[side].length)));
      node.append(heading);
      if (!lists[side].length) {
        node.append(element("p", "faceoff-badges__none", `Nothing ${player(side === "a" ? b : a).name} lacks`));
        return node;
      }
      const tiles = element("div", "faceoff-badges__tiles");
      lists[side].forEach((id) => {
        const tile = UI.badge(definitions.get(id), { earnedRaceIndex: held[side][id] });
        tile.style.setProperty("--i", String(order));
        order += 1;
        tiles.append(tile);
      });
      node.append(tiles);
      return node;
    };
    band.append(column("a"), spine, column("b"));
    return band;
  }

  // ---- Comments: each corner says something, from the same race when both had a say ----

  function cut(text) {
    return text.length > BUBBLE_MAX_LENGTH ? `${text.slice(0, BUBBLE_MAX_LENGTH - 1).trimEnd()}…` : text;
  }

  // A racer's first comment in each race, as the racer page reads them.
  function commentIn(data, race, playerIndex) {
    const row = (data.races[race.r] || []).find((comment) => comment[0] === player(playerIndex).id);
    return row ? row[1] : null;
  }

  // together: shared races where both commented. shared: shared races where only this corner did.
  // elsewhere: this corner's comments in races without the other.
  function buildPool(data, facts) {
    const pool = { together: [], shared: { a: [], b: [] }, elsewhere: { a: [], b: [] } };
    const sharedSet = new Set(facts ? facts.shared : []);
    if (facts) {
      facts.shared.forEach((raceIndex) => {
        const race = participants.races[raceIndex];
        const said = { a: commentIn(data, race, shown.a), b: commentIn(data, race, shown.b) };
        if (said.a !== null && said.b !== null) {
          pool.together.push({ raceIndex, a: said.a, b: said.b });
          return;
        }
        SIDES.forEach((side) => {
          if (said[side] !== null) {
            pool.shared[side].push({ raceIndex, text: said[side] });
          }
        });
      });
    }
    SIDES.forEach((side) => {
      if (shown[side] === null) {
        return;
      }
      raceIndexesByPlayer[shown[side]].forEach((raceIndex) => {
        if (sharedSet.has(raceIndex)) {
          return;
        }
        const text = commentIn(data, participants.races[raceIndex], shown[side]);
        if (text !== null) {
          pool.elsewhere[side].push({ raceIndex, text });
        }
      });
    });
    return pool;
  }

  // A random item, avoiding `previous` while the list has others.
  function randomItem(items, previous) {
    const choices = items.length > 1 ? items.filter((item) => item !== previous) : items;
    return choices.length ? choices[Math.floor(Math.random() * choices.length)] : null;
  }

  function drawFrom(pool) {
    const previous = lastDraw || {};
    if (pool.together.length) {
      return { together: randomItem(pool.together, previous.together) };
    }
    const draw = {};
    SIDES.forEach((side) => {
      const source = pool.shared[side].length ? pool.shared[side] : pool.elsewhere[side];
      draw[side] = randomItem(source, previous[side]);
    });
    return draw;
  }

  function poolSize(pool) {
    if (pool.together.length) {
      return pool.together.length;
    }
    return Math.max(...SIDES.map((side) => (pool.shared[side].length || pool.elsewhere[side].length)));
  }

  function bubble(side, text, raceIndex) {
    const race = participants.races[raceIndex];
    const entry = entryOf(race, shown[side]);
    const speech = UI.speech(cut(text), {
      size: "hero",
      context: [
        { text: placeText(entry) },
        { text: modeLabel(participants.modes[race.m]) },
        { text: UI.relativeDay(race.d), href: `${RACE_ROOT}${race.r}`, tooltip: formatRaceDate(race.d) },
      ],
    });
    speech.classList.add("faceoff-speech", `faceoff-speech--${side}`);
    if (text.length > BUBBLE_MAX_LENGTH) {
      bindTooltip(speech.querySelector(".ui-speech__bubble"), text);
    }
    return speech;
  }

  function paintComments() {
    const draw = drawFrom(commentPool);
    lastDraw = draw;
    const together = draw.together;
    SIDES.forEach((side) => {
      const host = parts[`say-${side}`];
      host.replaceChildren();
      if (together) {
        host.append(bubble(side, together[side], together.raceIndex));
      } else if (draw[side]) {
        host.append(bubble(side, draw[side].text, draw[side].raceIndex));
      }
    });
    parts.again.hidden = poolSize(commentPool) <= 1;
  }

  function drawComments(facts) {
    const mine = token;
    commentPool = null;
    lastDraw = null;
    SIDES.forEach((side) => parts[`say-${side}`].replaceChildren());
    if (shown.a === null && shown.b === null) {
      return;
    }
    UI.comments().then((data) => {
      if (mine !== token || !data || !data.races) {
        return;
      }
      commentPool = buildPool(data, facts);
      paintComments();
    });
  }

  // ---- Showing a pair ----

  function enter() {
    card.classList.remove("faceoff--enter");
    // A reflow restarts the entrance animations for the new pair.
    void card.offsetWidth;
    card.classList.add("faceoff--enter");
    clearTimeout(enterTimer);
    enterTimer = setTimeout(() => card.classList.remove("faceoff--enter"), ENTER_MS);
  }

  // The panel heading names the matchup once both corners are filled, and the status line says what changed.
  function announce() {
    const names = SIDES.filter((side) => shown[side] !== null).map((side) => player(shown[side]).name);
    const matchup = names.length === 2 ? `${names[0]} vs ${names[1]}` : null;
    document.querySelector("#faceoff-title").textContent = matchup ?? "Face off";
    const text = matchup ?? (names.length === 1 ? `${names[0]} waits for a challenger` : "Pick two racers to face off");
    document.querySelector("#faceoff-announcer").textContent = text;
  }

  // a and b are player indexes, or null for an empty corner.
  function show(a, b) {
    build();
    const arriving = !visible;
    visible = true;
    if (!arriving && a === shown.a && b === shown.b) {
      return;
    }
    token += 1;
    shown = { a, b };
    // A tooltip opened from something this render replaces would otherwise stay on screen.
    hideTooltip();
    card.dataset.picked = String(SIDES.filter((side) => shown[side] !== null).length);
    SIDES.forEach((side) => {
      pickers[side].sync();
      renderFrame(side);
      renderRate(side);
      renderChips(side);
    });
    renderSpine();
    const facts = a !== null && b !== null ? pairFacts(a, b) : null;
    const bands = facts ? [oddsBand(a, b, facts), tapeBand(a, b, facts), badgesBand(a, b)] : [];
    parts.body.replaceChildren(...bands);
    announce();
    enter();
    drawComments(facts);
    if (focusCorner !== null) {
      const link = parts[`frame-${focusCorner}`].querySelector("a");
      focusCorner = null;
      if (link) {
        link.focus({ preventScroll: true });
      }
    }
  }

  function hide() {
    visible = false;
    token += 1;
    if (pickers) {
      SIDES.forEach((side) => pickers[side].close());
    }
  }

  return { show, hide };
})();
