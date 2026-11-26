// 03 / Presets: what the ladder races, by mode or by one mode's presets, for a season or all time
// (docs/dashboard.md › Presets). Started and scheduled come from participants.preset_slots, the build's
// schedule join; every other number is computed here from participants.races, like the racer presets table.
const Presets = (() => {
  // A Fresh faces card shows this many avatars, then "+N".
  const FACES_SHOWN = 5;
  // A "+N" tooltip names this many more racers, then "and N more".
  const FACES_NAMED = 12;
  const SPOT_AVATAR_SIZE = 40;
  const FACE_AVATAR_SIZE = 30;
  const BEST_AVATAR_SIZE = 20;
  // The mix chart and the You column share one height and vertical margins, so their axes line up.
  const MIX_HEIGHT = 230;
  const MIX_MARGIN = { top: 14, right: 6, bottom: 30, left: 42 };
  const YOU_MARGIN = { left: 10, right: 10 };
  const MIX_TICKS = [0, 0.25, 0.5, 0.75, 1];
  const MIX_BAR_MAX = 34;
  const ALL_TIME = "all";
  const DEFAULT_SORT = { key: "started", direction: "desc" };
  const countedFormatter = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  });

  // season: a season number, or null for all time. mode: a mode id, or ALL_MODES. init fills both:
  // ALL_MODES belongs to leaderboard.js, which loads after this file.
  const state = { season: null, mode: null, sort: DEFAULT_SORT };
  const parts = {};
  let model = null;

  function element(tag, className = null, text = null) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== null) {
      node.textContent = text;
    }
    return node;
  }

  function plural(count, one, many = `${one}s`) {
    return `${formatCount(count)} ${count === 1 ? one : many}`;
  }

  function playerName(playerIndex) {
    return participants.players[playerIndex].name;
  }

  // Tooltip wording for the picked scope: "in Season 11 · Panda" or "all time".
  function scopeText() {
    return state.season === null ? "all time" : `in ${seasonLabel(state.season)}`;
  }

  function inScope(race) {
    return state.season === null || race.s === state.season;
  }

  function modeIdOf(race) {
    return participants.modes[race.m];
  }

  function presetNameOf(race) {
    return race.p === null ? null : participants.presets[race.p].n;
  }

  function presetLabel(name) {
    return name === null ? "No preset" : name;
  }

  // Modes in the data's order; a slot mode no race has (a future "unknown") goes last.
  function modeOrder(modeId) {
    const index = participants.modes.indexOf(modeId);
    return index === -1 ? participants.modes.length : index;
  }

  function byPresetName(left, right) {
    if (left === null || right === null) {
      return (left === null) - (right === null);
    }
    return left.localeCompare(right, "en", { sensitivity: "base" });
  }

  function sum(values) {
    return values.reduce((total, value) => total + value, 0);
  }

  function median(values) {
    const ordered = [...values].sort((left, right) => left - right);
    const middle = Math.floor(ordered.length / 2);
    return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
  }

  // Debuts and slots never change after load, so they are worked out once.
  function buildModel() {
    const races = participants.races;
    const order = races.map((_, index) => index).sort((left, right) => races[left].u - races[right].u || left - right);
    // Each racer's first included race, on the ladder and in each mode, as a race index.
    const ladderDebut = new Map();
    const modeDebut = new Map();
    order.forEach((raceIndex) => {
      const race = races[raceIndex];
      const modeId = modeIdOf(race);
      if (!modeDebut.has(modeId)) {
        modeDebut.set(modeId, new Map());
      }
      const debuts = modeDebut.get(modeId);
      race.e.forEach(([playerIndex]) => {
        if (!ladderDebut.has(playerIndex)) {
          ladderDebut.set(playerIndex, raceIndex);
        }
        if (!debuts.has(playerIndex)) {
          debuts.set(playerIndex, raceIndex);
        }
      });
    });
    const slots = participants.preset_slots.rows.map(([season, modeIndex, preset, scheduled, started]) => ({
      season,
      modeId: modeIndex === null ? UNKNOWN_MODE : participants.modes[modeIndex],
      preset,
      scheduled,
      started,
    }));
    const presetIndexes = new Map(
      participants.presets.map((preset, index) => [`${participants.modes[preset.m]}\u0000${preset.n}`, index]),
    );
    return { ladderDebut, modeDebut, slots, presetIndexes, mix: mixColumns() };
  }

  // Race entries per season and in all, by mode: what the mix chart stacks.
  function mixColumns() {
    const columns = participants.seasons.map((season) => ({ season: season.n, ended: season.ended, total: 0, byMode: new Map() }));
    columns.push({ season: null, ended: true, total: 0, byMode: new Map() });
    const bySeason = new Map(columns.map((column) => [column.season, column]));
    participants.races.forEach((race) => {
      const modeId = modeIdOf(race);
      [bySeason.get(race.s), bySeason.get(null)].forEach((column) => {
        if (column) {
          column.total += race.n;
          column.byMode.set(modeId, (column.byMode.get(modeId) ?? 0) + race.n);
        }
      });
    });
    return columns;
  }

  // Modes with a race or a counted slot in the scope, in mode order.
  function modesInScope() {
    const seen = new Set();
    model.slots.forEach((slot) => {
      if (state.season === null || slot.season === state.season) {
        seen.add(slot.modeId);
      }
    });
    participants.races.forEach((race) => {
      if (inScope(race)) {
        seen.add(modeIdOf(race));
      }
    });
    return [...seen].sort((left, right) => modeOrder(left) - modeOrder(right));
  }

  // Racers whose first race on the ladder (modeId null) or in a mode falls in the scope, newest debut first.
  // Under all time that is everyone, so the newest debuts lead.
  function freshFaces(modeId) {
    const debuts = modeId === null ? model.ladderDebut : model.modeDebut.get(modeId) ?? new Map();
    return [...debuts]
      .filter(([, raceIndex]) => inScope(participants.races[raceIndex]))
      .sort(([leftPlayer, left], [rightPlayer, right]) => (
        participants.races[right].u - participants.races[left].u || right - left || leftPlayer - rightPlayer
      ));
  }

  // Rows

  // One row per mode (All modes) or per preset of the picked mode, over the scope's counted slots and races.
  function boardRows() {
    const byMode = state.mode === ALL_MODES;
    const rows = new Map();
    const rowFor = (key) => {
      if (!rows.has(key)) {
        rows.set(key, { key, scheduled: 0, started: 0, raceIndexes: [] });
      }
      return rows.get(key);
    };
    model.slots.forEach((slot) => {
      if ((state.season !== null && slot.season !== state.season) || (!byMode && slot.modeId !== state.mode)) {
        return;
      }
      const row = rowFor(byMode ? slot.modeId : slot.preset);
      row.scheduled += slot.scheduled;
      row.started += slot.started;
    });
    participants.races.forEach((race, raceIndex) => {
      const modeId = modeIdOf(race);
      if (!inScope(race) || (!byMode && modeId !== state.mode)) {
        return;
      }
      rowFor(byMode ? modeId : presetNameOf(race)).raceIndexes.push(raceIndex);
    });
    const order = byMode
      ? (left, right) => modeOrder(left.key) - modeOrder(right.key)
      : (left, right) => byPresetName(left.key, right.key);
    return [...rows.values()].sort(order).map((row, rank) => describeRow(row, rank, byMode));
  }

  function describeRow(row, rank, byMode) {
    const pool = new Set();
    const fields = [];
    let best = null;
    row.raceIndexes.forEach((raceIndex) => {
      const race = participants.races[raceIndex];
      fields.push(race.n);
      race.e.forEach((entry) => {
        pool.add(entry[0]);
        // The fastest timed finish; the earliest race keeps a tie.
        const timed = isFinisher(entry) && typeof entry[3] === "number";
        if (!byMode && timed && (best === null || entry[3] < best.seconds)) {
          best = { playerIndex: entry[0], seconds: entry[3], raceIndex };
        }
      });
    });
    return {
      ...row,
      rank,
      pool,
      races: fields.length,
      mean: fields.length ? sum(fields) / fields.length : null,
      median: fields.length ? median(fields) : null,
      biggest: fields.length ? Math.max(...fields) : null,
      best,
      fresh: byMode && state.season !== null ? freshFaces(row.key).length : null,
    };
  }

  // Spotlights

  function spotCard(label, className = "") {
    const card = element("section", `presets-spot ${className}`.trim());
    card.appendChild(element("h4", "presets-spot__label", label));
    return card;
  }

  function emptySpot(card, text) {
    card.appendChild(element("p", "presets-spot__empty", text));
    return card;
  }

  function racerLink(playerIndex) {
    const link = element("a", "racer-link", playerName(playerIndex));
    link.href = playerHash(participants.players[playerIndex].id, ALL_MODES);
    return link;
  }

  // The headline racer of a card: a big ringed avatar, the linked name and a caption.
  function racerSpot(card, playerIndex, caption) {
    const body = element("div", "presets-spot__racer");
    body.appendChild(UI.markRacers(UI.avatar(playerIndex, SPOT_AVATAR_SIZE), [playerIndex]));
    const text = element("div", "presets-spot__text");
    text.append(racerLink(playerIndex), caption);
    body.appendChild(text);
    card.appendChild(body);
    return card;
  }

  function caption(text) {
    return element("p", "presets-spot__caption", text);
  }

  function globetrotterCard() {
    const card = spotCard("Globetrotter");
    const tallies = new Map();
    const modesRaced = new Set();
    participants.races.forEach((race) => {
      if (!inScope(race)) {
        return;
      }
      const modeId = modeIdOf(race);
      modesRaced.add(modeId);
      race.e.forEach(([playerIndex]) => {
        if (!tallies.has(playerIndex)) {
          tallies.set(playerIndex, { modes: new Set(), races: 0 });
        }
        const tally = tallies.get(playerIndex);
        tally.modes.add(modeId);
        tally.races += 1;
      });
    });
    // Most modes, then most races, then name.
    const ranked = [...tallies].sort(([leftPlayer, left], [rightPlayer, right]) => (
      right.modes.size - left.modes.size ||
      right.races - left.races ||
      playerName(leftPlayer).localeCompare(playerName(rightPlayer), "en", { sensitivity: "base" })
    ));
    if (!ranked.length) {
      return emptySpot(card, "No races yet");
    }
    const [playerIndex, tally] = ranked[0];
    const line = caption(`${formatCount(tally.modes.size)} of ${plural(modesRaced.size, "mode")} · ${plural(tally.races, "race")}`);
    const modes = [...tally.modes].sort((left, right) => modeOrder(left) - modeOrder(right)).map(modeLabel);
    line.tabIndex = 0;
    bindTooltip(line, `Raced ${modes.join(", ")} ${scopeText()}`);
    return racerSpot(card, playerIndex, line);
  }

  // The worst start rate among modes with enough scheduled races to judge and at least one that never
  // started: a call to action.
  function needsRacersCard(rows) {
    const card = spotCard("Needs racers");
    const minimum = participants.preset_min_races;
    const judged = rows.filter((row) => row.scheduled >= minimum);
    const ranked = judged
      .filter((row) => row.started < row.scheduled)
      .sort((left, right) => (
        left.started / left.scheduled - right.started / right.scheduled ||
        right.scheduled - left.scheduled ||
        left.rank - right.rank
      ));
    if (!ranked.length) {
      return emptySpot(card, judged.length
        ? `Every scheduled race started ${scopeText()}`
        : `No mode has ${minimum} scheduled races ${scopeText()}`);
    }
    const row = ranked[0];
    const body = element("div", "presets-spot__mode");
    body.appendChild(modeButton(row.key, "presets-spot__mode-name"));
    body.appendChild(caption(`${formatCount(row.started)} of ${plural(row.scheduled, "scheduled race")} started`));
    body.appendChild(caption(`${formatPercent(1 - row.started / row.scheduled)} never fired. Hop in!`));
    card.appendChild(body);
    return card;
  }

  function freshFacesCard(modeId) {
    const card = spotCard("Fresh faces");
    const faces = freshFaces(modeId);
    const where = modeId === null ? "the ladder" : modeLabel(modeId);
    if (!faces.length) {
      return emptySpot(card, modeId === null ? `Nobody new ${scopeText()}` : `Nobody new to ${where} ${scopeText()}`);
    }
    let text;
    if (state.season === null) {
      text = `Newest of ${plural(faces.length, modeId === null ? "racer" : `${where} racer`)}`;
    } else {
      text = modeId === null ? `${plural(faces.length, "racer")} joined the ladder` : `${plural(faces.length, "racer")} new to ${where}`;
    }
    card.appendChild(caption(text));
    const row = element("div", "presets-faces");
    faces.slice(0, FACES_SHOWN).forEach(([playerIndex, raceIndex]) => {
      const link = element("a", "presets-faces__face");
      link.href = playerHash(participants.players[playerIndex].id, modeId ?? ALL_MODES);
      link.setAttribute("aria-label", playerName(playerIndex));
      link.appendChild(UI.markRacers(UI.avatar(playerIndex, FACE_AVATAR_SIZE), [playerIndex]));
      const first = modeId === null ? "first race" : `first ${where} race`;
      bindTooltip(link, `${playerName(playerIndex)} · ${first} ${formatRaceDate(participants.races[raceIndex].d)}`);
      row.appendChild(link);
    });
    const rest = faces.slice(FACES_SHOWN);
    if (rest.length) {
      const more = element("span", "presets-faces__more", `+${formatCount(rest.length)}`);
      more.tabIndex = 0;
      const named = rest.slice(0, FACES_NAMED).map(([playerIndex]) => playerName(playerIndex));
      const tail = rest.length > FACES_NAMED ? `, and ${formatCount(rest.length - FACES_NAMED)} more` : "";
      bindTooltip(more, `${named.join(", ")}${tail}`);
      UI.markRacers(more, rest.map(([playerIndex]) => playerIndex));
      row.appendChild(more);
    }
    card.appendChild(row);
    return card;
  }

  function regularCard(rows) {
    const card = spotCard("The regular");
    const counts = new Map();
    rows.forEach((row) => row.raceIndexes.forEach((raceIndex) => {
      participants.races[raceIndex].e.forEach(([playerIndex]) => counts.set(playerIndex, (counts.get(playerIndex) ?? 0) + 1));
    }));
    const ranked = [...counts].sort(([leftPlayer, left], [rightPlayer, right]) => (
      right - left || playerName(leftPlayer).localeCompare(playerName(rightPlayer), "en", { sensitivity: "base" })
    ));
    if (!ranked.length) {
      return emptySpot(card, `No ${modeLabel(state.mode)} races ${scopeText()}`);
    }
    const [playerIndex, races] = ranked[0];
    return racerSpot(card, playerIndex, caption(`${plural(races, `${modeLabel(state.mode)} race`)}`));
  }

  function fastestCard(rows) {
    const card = spotCard("Fastest");
    const best = rows
      .filter((row) => row.best)
      .map((row) => ({ ...row.best, preset: row.key }))
      .sort((left, right) => (
        left.seconds - right.seconds || participants.races[left.raceIndex].u - participants.races[right.raceIndex].u
      ))[0];
    if (!best) {
      return emptySpot(card, `No timed ${modeLabel(state.mode)} finish ${scopeText()}`);
    }
    const line = element("p", "presets-spot__caption");
    const race = participants.races[best.raceIndex];
    const time = element("a", "presets-spot__time", formatDuration(best.seconds));
    time.href = RACE_ROOT + race.r;
    time.target = "_blank";
    time.rel = "noopener noreferrer";
    bindTooltip(time, `Set ${formatRaceDate(race.d)} · open the race`);
    line.append(time, ` · ${presetLabel(best.preset)}`);
    return racerSpot(card, best.playerIndex, line);
  }

  function renderSpotlights(rows) {
    const cards = state.mode === ALL_MODES
      ? [globetrotterCard(), needsRacersCard(rows), freshFacesCard(null)]
      : [regularCard(rows), fastestCard(rows), freshFacesCard(state.mode)];
    parts.spotlights.replaceChildren(...cards);
  }

  // The board

  // A mode's name that opens its presets, like pressing its toggle button.
  function modeButton(modeId, className = "presets-mode-link") {
    const button = element("button", className);
    button.type = "button";
    button.append(UI.modeDot(modeId), element("span", null, modeLabel(modeId)));
    bindTooltip(button, `Show ${modeLabel(modeId)} by preset`);
    button.addEventListener("click", () => pickMode(modeId));
    return button;
  }

  function dash(cell, why) {
    const node = element("span", "presets-dash", "–");
    node.tabIndex = 0;
    node.setAttribute("aria-label", why);
    bindTooltip(node, why);
    cell.appendChild(node);
  }

  // A bar sized against the column's largest value, then the value. fill is the solid share of the bar;
  // the rest is hatched (cancelled races).
  function meter(cell, { share, fill = 1, color, text, tip }) {
    const node = element("span", "presets-meter");
    node.tabIndex = 0;
    node.style.setProperty("--bar-color", color);
    const track = element("span", "presets-meter__track");
    const bar = element("span", "presets-meter__bar");
    bar.style.setProperty("--share", `${(share * 100).toFixed(2)}%`);
    bar.style.setProperty("--fill", `${(fill * 100).toFixed(2)}%`);
    track.appendChild(bar);
    node.append(track, element("span", "presets-meter__value", text));
    bindTooltip(node, tip);
    cell.appendChild(node);
  }

  function boardColumns(byMode, scale) {
    const color = (row) => modeColor(byMode ? row.key : state.mode);
    const where = (row) => (byMode ? modeLabel(row.key) : presetLabel(row.key));
    const columns = [
      byMode
        ? {
          key: "mode",
          label: "Mode",
          align: "left",
          defaultDirection: "asc",
          sortValue: (row) => modeLabel(row.key),
          render: (cell, row) => cell.appendChild(modeButton(row.key)),
        }
        : {
          key: "preset",
          label: "Preset",
          align: "left",
          defaultDirection: "asc",
          sortValue: (row) => presetLabel(row.key),
          render: (cell, row) => {
            cell.appendChild(element("span", row.key === null ? "preset-name presets-muted" : "preset-name", presetLabel(row.key)));
            if (model.presetIndexes.get(`${state.mode}\u0000${row.key}`) === participants.hardest_preset) {
              cell.append(" ", UI.tag("hardest", "Hardest", `Highest DNF rate among presets with ${participants.preset_min_races}+ races`));
            }
          },
        },
      {
        key: "started",
        label: "Started / scheduled",
        sortLabel: "races started",
        tooltip: "Scheduled races that started. The hatched part was scheduled but never started.",
        sortValue: (row) => (row.scheduled ? row.started : null),
        render: (cell, row) => {
          if (!row.scheduled) {
            dash(cell, `No scheduled ${where(row)} race counted ${scopeText()}`);
            return;
          }
          const cancelled = row.scheduled - row.started;
          meter(cell, {
            share: row.scheduled / scale.scheduled,
            fill: row.started / row.scheduled,
            color: color(row),
            text: `${formatCount(row.started)}/${formatCount(row.scheduled)}`,
            tip: `${where(row)}: ${formatCount(row.started)} of ${plural(row.scheduled, "scheduled race")} started (${formatPercent(row.started / row.scheduled)}), ${formatCount(cancelled)} never did`,
          });
        },
      },
      {
        key: "pool",
        label: "Racer pool",
        sortLabel: "racer pool",
        tooltip: "Distinct racers in its races",
        sortValue: (row) => row.pool.size || null,
        render: (cell, row) => {
          if (!row.pool.size) {
            dash(cell, `No ${where(row)} race started ${scopeText()}`);
            return;
          }
          const fresh = row.fresh === null ? "" : `, ${formatCount(row.fresh)} new to it`;
          meter(cell, {
            share: row.pool.size / scale.pool,
            color: color(row),
            text: formatCount(row.pool.size),
            tip: `${where(row)}: ${plural(row.pool.size, "racer")} ${scopeText()}${fresh}`,
          });
        },
      },
      {
        key: "room",
        label: "Room size",
        sortLabel: "room size",
        tooltip: "Average racers per race that started",
        sortValue: (row) => row.mean,
        render: (cell, row) => {
          if (row.mean === null) {
            dash(cell, `No ${where(row)} race started ${scopeText()}`);
            return;
          }
          meter(cell, {
            share: row.mean / scale.mean,
            color: color(row),
            text: formatAverage(row.mean),
            tip: `${where(row)}: ${formatAverage(row.mean)} racers per race over ${plural(row.races, "race")}, median ${formatCount(row.median)}, biggest room ${formatCount(row.biggest)}`,
          });
        },
      },
    ];
    if (!byMode) {
      columns.push({
        key: "best",
        label: state.season === null ? "Record" : "Season best",
        sortLabel: "fastest time",
        defaultDirection: "asc",
        sortValue: (row) => row.best?.seconds ?? null,
        render: (cell, row) => {
          if (!row.best) {
            dash(cell, `No timed finish ${scopeText()}`);
            return;
          }
          const racer = element("span", "presets-racer");
          racer.append(UI.avatar(row.best.playerIndex, BEST_AVATAR_SIZE), racerLink(row.best.playerIndex));
          UI.markRacers(racer, [row.best.playerIndex]);
          const best = element("span", "presets-best");
          best.append(UI.time(row.best.seconds), racer);
          cell.appendChild(best);
        },
      });
    }
    return columns;
  }

  function renderBoard(rows) {
    const byMode = state.mode === ALL_MODES;
    parts.boardTitle.textContent = byMode ? "By mode" : `${modeLabel(state.mode)} by preset`;
    const most = (value) => Math.max(1, ...rows.map(value));
    const scale = {
      scheduled: most((row) => row.scheduled),
      pool: most((row) => row.pool.size),
      mean: Math.max(1, ...rows.map((row) => row.mean ?? 0)),
    };
    const columns = boardColumns(byMode, scale);
    const sortState = { sort: state.sort };
    renderSortableTable(parts.board, columns, sortRows(rows, columns, state.sort), sortState, (column) => {
      state.sort = nextSort(sortState, column);
      renderBoard(rows);
    }, { rowRacers: (row) => [...row.pool] });
  }

  function renderNote() {
    const counted = participants.preset_slots.counted_through;
    const key = element("span", "presets-key");
    parts.boardNote.replaceChildren(key, "Never started");
    // A time the browser cannot read only loses this line, not the section.
    const time = counted === null ? NaN : Date.parse(counted);
    if (!Number.isNaN(time)) {
      parts.boardNote.append(` · scheduled races counted through ${countedFormatter.format(new Date(time))}`);
    }
  }

  // Mode mix by season

  function columnName(column) {
    if (column.season === null) {
      return "All time";
    }
    return `${seasonLabel(column.season)}${column.ended ? "" : " so far"}`;
  }

  function mixLabel(column) {
    if (!column.total) {
      return `${columnName(column)}: no races`;
    }
    const shares = [...column.byMode]
      .sort(([leftMode, left], [rightMode, right]) => right - left || modeOrder(leftMode) - modeOrder(rightMode))
      .map(([modeId, count]) => `${modeLabel(modeId)} ${formatPercent(count / column.total)}`);
    return `${columnName(column)}, share of race entries: ${shares.join(" · ")}`;
  }

  function mixAxis(svg, left, right, plotHeight, labels) {
    MIX_TICKS.forEach((tick) => {
      const y = MIX_MARGIN.top + plotHeight * (1 - tick);
      svg.append(svgElement("line", { class: tick === 0 ? "baseline" : "grid-line", x1: left, x2: right, y1: y, y2: y }));
      if (labels) {
        svg.append(svgElement("text", { class: "axis-label", x: left - 8, y: y + 4, "text-anchor": "end" }, formatPercent(tick)));
      }
    });
  }

  // One bar of counts stacked by mode, from the baseline up.
  function mixBar(group, x, width, plotHeight, counts, total) {
    let below = 0;
    counts.forEach(([modeId, count]) => {
      const height = (count / total) * plotHeight;
      if (height <= 0) {
        return;
      }
      const y = MIX_MARGIN.top + plotHeight - below - height;
      const segment = svgElement("rect", { class: "bar presets-mix__segment", x, y, width, height });
      segment.style.fill = modeColor(modeId);
      group.appendChild(segment);
      below += height;
    });
  }

  // Counts by mode in mode order, the first at the bottom of the stack.
  function inModeOrder(byMode) {
    return [...byMode].sort(([left], [right]) => modeOrder(left) - modeOrder(right));
  }

  function mixChart(width) {
    const { top, right, bottom, left } = MIX_MARGIN;
    const plotHeight = MIX_HEIGHT - top - bottom;
    const columns = model.mix;
    // All sits half a band apart from the seasons.
    const band = (width - left - right) / (columns.length + 0.5);
    const barWidth = Math.min(MIX_BAR_MAX, band * 0.66);
    const svg = svgElement("svg", {
      class: "chart-svg presets-mix",
      width,
      height: MIX_HEIGHT,
      viewBox: `0 0 ${width} ${MIX_HEIGHT}`,
      role: "group",
      "aria-labelledby": "presets-mix-title",
    });
    mixAxis(svg, left, width - right, plotHeight, true);
    columns.forEach((column, index) => {
      const bandX = left + (index + (column.season === null ? 0.5 : 0)) * band;
      const x = bandX + (band - barWidth) / 2;
      const label = mixLabel(column);
      const group = svgElement("g", {
        class: "chart-mark presets-mix__column",
        tabindex: "0",
        role: "button",
        "aria-pressed": String(column.season === state.season),
        "aria-label": `${label}. Show ${column.season === null ? "all time" : seasonLabel(column.season)}`,
      });
      group.dataset.season = column.season === null ? ALL_TIME : String(column.season);
      group.append(
        svgElement("rect", { class: "bar-hit", x: bandX, y: top, width: band, height: plotHeight + bottom }),
        svgElement("rect", { class: "presets-mix__pick", x: x - 4, y: top - 4, width: barWidth + 8, height: plotHeight + 8, rx: 3 }),
      );
      if (column.total) {
        mixBar(group, x, barWidth, plotHeight, inModeOrder(column.byMode), column.total);
      }
      group.append(
        svgElement("rect", { class: "focus-ring", x: x - 4, y: top - 4, width: barWidth + 8, height: plotHeight + 8, rx: 3 }),
        svgElement(
          "text",
          { class: "axis-label presets-mix__label", x: x + barWidth / 2, y: MIX_HEIGHT - 10, "text-anchor": "middle" },
          column.season === null ? "All" : String(column.season),
        ),
      );
      group.addEventListener("click", () => pickSeason(column.season));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          pickSeason(column.season);
        }
      });
      bindTooltip(group, label);
      svg.appendChild(group);
    });
    return svg;
  }

  // The chart is drawn once, for All modes; the picked season changes only which column is outlined, so the
  // chart keeps its nodes (and focus).
  function syncMixPick() {
    const picked = state.season === null ? ALL_TIME : String(state.season);
    parts.mix.querySelectorAll(".presets-mix__column").forEach((group) => {
      group.setAttribute("aria-pressed", String(group.dataset.season === picked));
    });
  }

  // Your own races by mode in the scope, beside the mix chart. It exists only while a racer is set
  // ("This is me"), and refills in place when that changes.
  function renderYou() {
    const me = meIndex();
    parts.you.hidden = me === null;
    if (me === null) {
      parts.you.replaceChildren();
      return;
    }
    const byMode = new Map();
    let total = 0;
    participants.races.forEach((race) => {
      if (inScope(race) && race.e.some(([playerIndex]) => playerIndex === me)) {
        const modeId = modeIdOf(race);
        byMode.set(modeId, (byMode.get(modeId) ?? 0) + 1);
        total += 1;
      }
    });
    const width = YOU_MARGIN.left + MIX_BAR_MAX + YOU_MARGIN.right;
    const plotHeight = MIX_HEIGHT - MIX_MARGIN.top - MIX_MARGIN.bottom;
    const svg = svgElement("svg", {
      class: "chart-svg presets-mix presets-mix--you",
      width,
      height: MIX_HEIGHT,
      viewBox: `0 0 ${width} ${MIX_HEIGHT}`,
      role: "group",
      "aria-label": "Your mix",
    });
    mixAxis(svg, 0, width, plotHeight, false);
    let label;
    if (!total) {
      label = `You have no races ${scopeText()}`;
    } else {
      const shares = [...byMode]
        .sort(([leftMode, left], [rightMode, right]) => right - left || modeOrder(leftMode) - modeOrder(rightMode))
        .map(([modeId, count]) => `${modeLabel(modeId)} ${formatPercent(count / total)}`);
      label = `You ${scopeText()}, share of your races: ${shares.join(" · ")}`;
    }
    const group = svgElement("g", { class: "chart-mark", tabindex: "0", role: "img", "aria-label": label });
    group.appendChild(svgElement("rect", { class: "bar-hit", x: 0, y: MIX_MARGIN.top, width, height: plotHeight }));
    if (total) {
      mixBar(group, YOU_MARGIN.left, MIX_BAR_MAX, plotHeight, inModeOrder(byMode), total);
    }
    group.append(
      svgElement("rect", { class: "focus-ring", x: YOU_MARGIN.left - 4, y: MIX_MARGIN.top - 4, width: MIX_BAR_MAX + 8, height: plotHeight + 8, rx: 3 }),
      svgElement("text", { class: "axis-label presets-mix__you", x: width / 2, y: MIX_HEIGHT - 10, "text-anchor": "middle" }, "You"),
    );
    bindTooltip(group, label);
    svg.appendChild(group);
    parts.you.replaceChildren(svg);
  }

  // Crossover

  function renderCrossover() {
    const show = state.mode !== ALL_MODES;
    parts.crossBlock.hidden = !show;
    if (!show) {
      parts.cross.replaceChildren();
      return;
    }
    const mode = modeLabel(state.mode);
    parts.crossTitle.textContent = `Where ${mode} racers also race`;
    const pools = new Map();
    participants.races.forEach((race) => {
      if (!inScope(race)) {
        return;
      }
      const modeId = modeIdOf(race);
      if (!pools.has(modeId)) {
        pools.set(modeId, new Set());
      }
      race.e.forEach(([playerIndex]) => pools.get(modeId).add(playerIndex));
    });
    const pool = pools.get(state.mode);
    if (!pool) {
      parts.crossNote.textContent = "";
      parts.cross.replaceChildren(element("li", "presets-cross__empty", `No ${mode} race started ${scopeText()}`));
      return;
    }
    parts.crossNote.textContent = `Share of its ${plural(pool.size, "racer")} who raced each other mode`;
    const others = [...pools]
      .filter(([modeId]) => modeId !== state.mode)
      .map(([modeId, otherPool]) => ({ modeId, racers: [...pool].filter((playerIndex) => otherPool.has(playerIndex)) }))
      .filter((other) => other.racers.length)
      .sort((left, right) => right.racers.length - left.racers.length || modeOrder(left.modeId) - modeOrder(right.modeId));
    const only = [...pool].filter((playerIndex) => others.every((other) => !other.racers.includes(playerIndex)));
    const rows = others.map((other) => crossRow({
      dot: UI.modeDot(other.modeId),
      label: modeLabel(other.modeId),
      color: modeColor(other.modeId),
      racers: other.racers,
      total: pool.size,
      tip: `${formatCount(other.racers.length)} of ${plural(pool.size, `${mode} racer`)} ${scopeText()} also raced ${modeLabel(other.modeId)}`,
    }));
    rows.push(crossRow({
      dot: UI.modeDot(state.mode),
      label: `Only ${mode}`,
      color: modeColor(state.mode),
      racers: only,
      total: pool.size,
      tip: `${formatCount(only.length)} of ${plural(pool.size, `${mode} racer`)} ${scopeText()} raced no other mode`,
      only: true,
    }));
    parts.cross.replaceChildren(...rows);
  }

  function crossRow({ dot, label, color, racers, total, tip, only = false }) {
    const row = element("li", only ? "presets-cross__row presets-cross__row--only" : "presets-cross__row");
    UI.markRacers(row, racers);
    const name = element("span", "presets-cross__name");
    name.append(dot, element("span", null, label));
    const cell = element("span", "presets-cross__meter");
    meter(cell, { share: racers.length / total, color, text: formatPercent(racers.length / total), tip });
    row.append(name, cell);
    return row;
  }

  // Picking

  function render() {
    // The control that was clicked may be replaced below, so its tooltip would never get its mouseleave.
    hideTooltip();
    renderModeToggle(parts.toggle, modesInScope(), state.mode, pickMode);
    const rows = boardRows();
    renderSpotlights(rows);
    renderBoard(rows);
    parts.mixBlock.hidden = state.mode !== ALL_MODES;
    syncMixPick();
    renderYou();
    renderCrossover();
  }

  function pickSeason(season) {
    state.season = season;
    state.sort = DEFAULT_SORT;
    parts.select.value = season === null ? ALL_TIME : String(season);
    // A mode the season never ran falls back to All modes.
    if (state.mode !== ALL_MODES && !modesInScope().includes(state.mode)) {
      state.mode = ALL_MODES;
    }
    render();
  }

  function pickMode(modeId) {
    state.mode = modeId;
    state.sort = DEFAULT_SORT;
    render();
  }

  function bindSeasonSelect() {
    const seasons = participants.seasons.map((season) => season.n).reverse();
    parts.select.replaceChildren(
      ...seasons.map((season) => new Option(seasonLabel(season), String(season))),
      new Option("All time", ALL_TIME),
    );
    parts.select.addEventListener("change", () => {
      pickSeason(parts.select.value === ALL_TIME ? null : Number(parts.select.value));
    });
  }

  // Called by loadParticipants in the task that shows the other racer sections, so this one appears with them.
  function init() {
    const section = document.querySelector("#presets");
    if (!section) {
      return;
    }
    Object.assign(parts, {
      toggle: document.querySelector("#presets-mode-toggle"),
      select: document.querySelector("#presets-season"),
      spotlights: document.querySelector("#presets-spotlights"),
      boardTitle: document.querySelector("#presets-board-title"),
      boardNote: document.querySelector("#presets-board-note"),
      board: document.querySelector("#presets-board"),
      mixBlock: document.querySelector("#presets-mix-block"),
      mix: document.querySelector("#presets-mix"),
      you: document.querySelector("#presets-you"),
      crossBlock: document.querySelector("#presets-cross-block"),
      crossTitle: document.querySelector("#presets-cross-title"),
      crossNote: document.querySelector("#presets-cross-note"),
      cross: document.querySelector("#presets-cross"),
    });
    model = buildModel();
    state.mode = ALL_MODES;
    state.season = currentSeason();
    bindSeasonSelect();
    parts.select.value = state.season === null ? ALL_TIME : String(state.season);
    document.querySelector("#presets-season-control").hidden = participants.seasons.length === 0;
    renderNote();
    mountChart(parts.mix, mixChart);
    render();
    document.addEventListener(ME_EVENT, renderYou);
    section.hidden = false;
  }

  return { init };
})();
