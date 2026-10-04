const PARTICIPANT_SCHEMA_VERSION = 1;
const ALL_MODES = "all";
const DONE_STATUS = 0;
const TREND_WINDOW = 5;
const TREND_THRESHOLD = 0.05;
const MIN_RACES_THRESHOLD = 5;
const RACE_ROOT = "https://racetime.gg/";
const BASE_TITLE = document.title;

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

let participants = null;
let playerIndexById = new Map();
let raceIndexesByPlayer = [];
let recordCache = new Map();
let currentView = null;

const leaderboardState = { mode: ALL_MODES, sort: null, query: "", minRaces: 0 };
const playerState = { mode: ALL_MODES, sort: null, query: "" };

function validateParticipants(data) {
  if (!data || data.schema_version !== PARTICIPANT_SCHEMA_VERSION) {
    throw new Error("Unexpected racer data schema version.");
  }
  if (
    !Array.isArray(data.players) ||
    !Array.isArray(data.races) ||
    !Array.isArray(data.modes) ||
    !Array.isArray(data.leaderboards)
  ) {
    throw new Error("Racer data is missing required collections.");
  }
  if (!data.players.length || !data.races.length) {
    throw new Error("Racer data is empty.");
  }
  data.races.forEach((race) => {
    if (!Array.isArray(race.e) || !Number.isInteger(race.m) || race.m < 0 || race.m >= data.modes.length) {
      throw new Error("Racer data contains an invalid race record.");
    }
    race.e.forEach((entry) => {
      if (!Array.isArray(entry) || !Number.isInteger(entry[0]) || entry[0] < 0 || entry[0] >= data.players.length) {
        throw new Error("Racer data contains an out-of-range player index.");
      }
    });
  });
  const modes = data.leaderboards.map((board) => board.mode);
  if (modes[0] !== ALL_MODES) {
    throw new Error("Racer data is missing the combined leaderboard.");
  }
}

function buildIndexes(data) {
  playerIndexById = new Map(data.players.map((player, index) => [player.id, index]));
  raceIndexesByPlayer = data.players.map(() => []);
  data.races.forEach((race, raceIndex) => {
    race.e.forEach((entry) => raceIndexesByPlayer[entry[0]].push(raceIndex));
  });
}

function modeIndexFor(modeId) {
  return modeId === ALL_MODES ? null : participants.modes.indexOf(modeId);
}

function modeLabel(modeId) {
  return modeId === ALL_MODES ? "All modes" : modeId.replaceAll("_", " ");
}

function isFinisher(entry) {
  return entry[2] === DONE_STATUS && entry[1] !== null;
}

// Lower place wins; any finisher beats a non-finisher; two non-finishers draw.
function compareEntries(subject, opponent) {
  const subjectFinished = isFinisher(subject);
  const opponentFinished = isFinisher(opponent);
  if (subjectFinished && opponentFinished) {
    if (subject[1] === opponent[1]) {
      return 0;
    }
    return subject[1] < opponent[1] ? 1 : -1;
  }
  if (subjectFinished) {
    return 1;
  }
  if (opponentFinished) {
    return -1;
  }
  return 0;
}

function recordsForMode(modeId) {
  if (recordCache.has(modeId)) {
    return recordCache.get(modeId);
  }
  const modeIndex = modeIndexFor(modeId);
  const records = participants.players.map(() => ({ wins: 0, losses: 0, draws: 0 }));
  participants.races.forEach((race) => {
    if (modeIndex !== null && race.m !== modeIndex) {
      return;
    }
    const entries = race.e;
    for (let left = 0; left < entries.length; left += 1) {
      for (let right = left + 1; right < entries.length; right += 1) {
        const outcome = compareEntries(entries[left], entries[right]);
        const first = records[entries[left][0]];
        const second = records[entries[right][0]];
        if (outcome > 0) {
          first.wins += 1;
          second.losses += 1;
        } else if (outcome < 0) {
          first.losses += 1;
          second.wins += 1;
        } else {
          first.draws += 1;
          second.draws += 1;
        }
      }
    }
  });
  recordCache.set(modeId, records);
  return records;
}

function computeHeadToHead(playerIndex, modeId) {
  const modeIndex = modeIndexFor(modeId);
  const opponents = new Map();
  raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
    const race = participants.races[raceIndex];
    if (modeIndex !== null && race.m !== modeIndex) {
      return;
    }
    const subject = race.e.find((entry) => entry[0] === playerIndex);
    race.e.forEach((entry) => {
      if (entry[0] === playerIndex) {
        return;
      }
      let record = opponents.get(entry[0]);
      if (!record) {
        record = {
          opponent: entry[0],
          wins: 0,
          losses: 0,
          draws: 0,
          meetings: 0,
          sequence: [],
          lastRaceIndex: raceIndex,
        };
        opponents.set(entry[0], record);
      }
      const outcome = compareEntries(subject, entry);
      record.meetings += 1;
      record.lastRaceIndex = raceIndex;
      record.sequence.push(outcome);
      if (outcome > 0) {
        record.wins += 1;
      } else if (outcome < 0) {
        record.losses += 1;
      } else {
        record.draws += 1;
      }
    });
  });
  return [...opponents.values()];
}

function winRate(wins, losses) {
  const decisive = wins + losses;
  return decisive ? wins / decisive : null;
}

function trendFor(sequence) {
  if (sequence.length <= TREND_WINDOW) {
    return null;
  }
  const decisive = sequence.filter((value) => value !== 0);
  if (decisive.length <= TREND_WINDOW) {
    return null;
  }
  const career = decisive.filter((value) => value > 0).length / decisive.length;
  const recent = decisive.slice(-TREND_WINDOW);
  const recentRate = recent.filter((value) => value > 0).length / recent.length;
  const delta = recentRate - career;
  if (delta > TREND_THRESHOLD) {
    return { delta, direction: "up" };
  }
  if (delta < -TREND_THRESHOLD) {
    return { delta, direction: "down" };
  }
  return { delta, direction: "flat" };
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) {
    return "--";
  }
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function formatRate(value) {
  return value === null ? "--" : `${Math.round(value * 100)}%`;
}

function formatRaceDate(isoDate) {
  return dateFormatter.format(new Date(`${isoDate}T00:00:00Z`));
}

function playerHash(playerId, modeId) {
  const suffix = modeId === ALL_MODES ? "" : `/${encodeURIComponent(modeId)}`;
  return `#/player/${encodeURIComponent(playerId)}${suffix}`;
}

function appendRacerLink(cell, player, modeId, rank) {
  if (rank !== null) {
    const badge = document.createElement("span");
    badge.className = "rank-badge";
    badge.textContent = integerFormatter.format(rank);
    cell.appendChild(badge);
  }
  const link = document.createElement("a");
  link.className = "racer-link";
  link.href = playerHash(player.id, modeId);
  link.textContent = player.name;
  cell.appendChild(link);
}

function appendText(cell, text) {
  cell.textContent = text;
}

function appendMedal(cell, value, isGold) {
  cell.textContent = integerFormatter.format(value);
  if (isGold && value > 0) {
    cell.classList.add("medal-count--first");
  }
}

function appendTrend(cell, trend) {
  if (!trend) {
    cell.textContent = "--";
    return;
  }
  const points = Math.round(Math.abs(trend.delta) * 100);
  const glyph = trend.direction === "up" ? "\u25B2" : trend.direction === "down" ? "\u25BC" : "\u25AC";
  const sign = trend.direction === "up" ? "+" : trend.direction === "down" ? "-" : "\u00B1";
  const span = document.createElement("span");
  span.className = `trend trend--${trend.direction}`;
  span.textContent = `${glyph} ${sign}${points}%`;
  cell.appendChild(span);
  const wording =
    trend.direction === "up"
      ? `Trending up, ${points} points above`
      : trend.direction === "down"
        ? `Trending down, ${points} points below`
        : "Holding steady against";
  cell.setAttribute("aria-label", `${wording} the career rate over the last ${TREND_WINDOW} decisive meetings`);
}

function sortRows(rows, columns, sort) {
  if (!sort) {
    return rows;
  }
  const column = columns.find((item) => item.key === sort.key);
  if (!column) {
    return rows;
  }
  const factor = sort.direction === "asc" ? 1 : -1;
  return [...rows].sort((left, right) => {
    const a = column.sortValue(left);
    const b = column.sortValue(right);
    const aMissing = a === null || a === undefined;
    const bMissing = b === null || b === undefined;
    if (aMissing || bMissing) {
      if (aMissing && bMissing) {
        return left.rank - right.rank;
      }
      return aMissing ? 1 : -1;
    }
    if (typeof a === "string" || typeof b === "string") {
      const compared = String(a).localeCompare(String(b));
      return compared ? compared * factor : left.rank - right.rank;
    }
    return a === b ? left.rank - right.rank : (a - b) * factor;
  });
}

function nextSort(state, column) {
  if (state.sort && state.sort.key === column.key) {
    return { key: column.key, direction: state.sort.direction === "asc" ? "desc" : "asc" };
  }
  return { key: column.key, direction: column.defaultDirection || "desc" };
}

function renderSortableTable(container, columns, rows, state, onSort) {
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");

  columns.forEach((column) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.classList.add(`align-${column.align || "right"}`);
    const active = state.sort && state.sort.key === column.key;
    cell.setAttribute(
      "aria-sort",
      active ? (state.sort.direction === "asc" ? "ascending" : "descending") : "none",
    );
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sort-button";
    button.textContent = column.label;
    button.setAttribute("aria-label", `Sort by ${column.sortLabel || column.label}`);
    button.addEventListener("click", () => onSort(column));
    cell.appendChild(button);
    headRow.appendChild(cell);
  });

  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const bodyRow = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("td");
      cell.classList.add(`align-${column.align || "right"}`);
      column.render(cell, row);
      bodyRow.appendChild(cell);
    });
    body.appendChild(bodyRow);
  });
  table.appendChild(body);
  container.replaceChildren(table);
}

function renderModeToggle(container, modes, activeMode, onSelect) {
  const buttons = [ALL_MODES, ...modes].map((mode) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mode-toggle__button";
    button.textContent = modeLabel(mode);
    button.setAttribute("aria-pressed", String(mode === activeMode));
    button.addEventListener("click", () => onSelect(mode));
    return button;
  });
  container.replaceChildren(...buttons);
}

function announce(elementId, message) {
  document.querySelector(elementId).textContent = message;
}

function matchesQuery(name, query) {
  return !query || name.toLowerCase().includes(query);
}

function leaderboardColumns() {
  return [
    {
      key: "rank",
      label: "Racer",
      sortLabel: "leaderboard rank",
      align: "left",
      defaultDirection: "asc",
      sortValue: (row) => row.rank,
      render: (cell, row) => appendRacerLink(cell, row.player, leaderboardState.mode, row.rank),
    },
    {
      key: "races",
      label: "Races",
      sortValue: (row) => row.races,
      render: (cell, row) => appendText(cell, integerFormatter.format(row.races)),
    },
    {
      key: "first",
      label: "1st",
      sortLabel: "first places",
      sortValue: (row) => row.first,
      render: (cell, row) => appendMedal(cell, row.first, true),
    },
    {
      key: "second",
      label: "2nd",
      sortLabel: "second places",
      sortValue: (row) => row.second,
      render: (cell, row) => appendMedal(cell, row.second, false),
    },
    {
      key: "third",
      label: "3rd",
      sortLabel: "third places",
      sortValue: (row) => row.third,
      render: (cell, row) => appendMedal(cell, row.third, false),
    },
    {
      key: "unfinished",
      label: "DNF",
      sortLabel: "races not finished",
      sortValue: (row) => row.unfinished,
      render: (cell, row) => appendText(cell, integerFormatter.format(row.unfinished)),
    },
    {
      key: "winRate",
      label: "Win %",
      sortLabel: "head-to-head win rate",
      sortValue: (row) => row.winRate,
      render: (cell, row) => appendText(cell, formatRate(row.winRate)),
    },
    {
      key: "average",
      label: "Avg time",
      sortLabel: "average finish time",
      defaultDirection: "asc",
      sortValue: (row) => row.averageSeconds,
      render: (cell, row) => appendText(cell, formatDuration(row.averageSeconds)),
    },
  ];
}

function leaderboardRows(modeId) {
  const board = participants.leaderboards.find((item) => item.mode === modeId);
  if (!board) {
    return [];
  }
  const records = recordsForMode(modeId);
  return board.rows.map((row, index) => {
    const record = records[row.p];
    return {
      rank: index + 1,
      player: participants.players[row.p],
      races: row.races,
      first: row.first,
      second: row.second,
      third: row.third,
      unfinished: row.dnf + row.dq,
      averageSeconds: row.average_seconds,
      winRate: winRate(record.wins, record.losses),
    };
  });
}

function renderLeaderboard() {
  const container = document.querySelector("#leaderboard-table");
  renderModeToggle(
    document.querySelector("#leaderboard-mode-toggle"),
    participants.modes,
    leaderboardState.mode,
    (mode) => {
      leaderboardState.mode = mode;
      leaderboardState.sort = null;
      renderLeaderboard();
    },
  );

  const columns = leaderboardColumns();
  const all = leaderboardRows(leaderboardState.mode);
  const visible = all.filter(
    (row) =>
      row.races >= leaderboardState.minRaces &&
      matchesQuery(row.player.name, leaderboardState.query),
  );
  document
    .querySelector("#leaderboard-min-races")
    .setAttribute("aria-pressed", String(leaderboardState.minRaces > 0));
  renderSortableTable(container, columns, sortRows(visible, columns, leaderboardState.sort), leaderboardState, (column) => {
    leaderboardState.sort = nextSort(leaderboardState, column);
    renderLeaderboard();
  });

  const sortLabel = leaderboardState.sort
    ? `Sorted by ${columns.find((item) => item.key === leaderboardState.sort.key).sortLabel || ""}, ${leaderboardState.sort.direction === "asc" ? "ascending" : "descending"}. `
    : "";
  const filterLabel = leaderboardState.minRaces > 0 ? `, ${MIN_RACES_THRESHOLD}+ races only` : "";
  announce("#leaderboard-announcer", `${sortLabel}${visible.length} of ${all.length} racers shown for ${modeLabel(leaderboardState.mode)}${filterLabel}.`);
}

function playerModes(playerIndex) {
  const seen = new Set();
  raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
    seen.add(participants.modes[participants.races[raceIndex].m]);
  });
  return participants.modes.filter((mode) => seen.has(mode));
}

function summaryItem(label, value) {
  const item = document.createElement("div");
  item.className = "summary-item";
  const caption = document.createElement("span");
  caption.className = "summary-label";
  caption.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = value;
  item.append(caption, strong);
  return item;
}

function renderPlayerSummary(playerIndex, modeId, matchups) {
  const board = participants.leaderboards.find((item) => item.mode === modeId);
  const row = board ? board.rows.find((item) => item.p === playerIndex) : null;
  const totals = matchups.reduce(
    (carry, matchup) => ({
      wins: carry.wins + matchup.wins,
      losses: carry.losses + matchup.losses,
      draws: carry.draws + matchup.draws,
    }),
    { wins: 0, losses: 0, draws: 0 },
  );
  const container = document.querySelector("#player-summary");
  container.replaceChildren(
    summaryItem("Races", row ? integerFormatter.format(row.races) : "0"),
    summaryItem("Podiums", row ? `${row.first} / ${row.second} / ${row.third}` : "--"),
    summaryItem("Record W-L-D", `${totals.wins}-${totals.losses}-${totals.draws}`),
    summaryItem("Win rate", formatRate(winRate(totals.wins, totals.losses))),
    summaryItem("DNF / DQ", row ? `${row.dnf} / ${row.dq}` : "--"),
    summaryItem("Median place", row && row.median_place !== null ? decimalFormatter.format(row.median_place) : "--"),
    summaryItem("Average", row ? formatDuration(row.average_seconds) : "--"),
    summaryItem("Best", row ? formatDuration(row.min_seconds) : "--"),
    summaryItem("Worst", row ? formatDuration(row.max_seconds) : "--"),
  );
}

function headToHeadColumns() {
  return [
    {
      key: "opponent",
      label: "Opponent",
      align: "left",
      defaultDirection: "asc",
      sortValue: (row) => row.player.name.toLowerCase(),
      render: (cell, row) => appendRacerLink(cell, row.player, playerState.mode, null),
    },
    {
      key: "meetings",
      label: "Races",
      sortLabel: "races together",
      sortValue: (row) => row.meetings,
      render: (cell, row) => appendText(cell, integerFormatter.format(row.meetings)),
    },
    {
      key: "wins",
      label: "Won",
      sortValue: (row) => row.wins,
      render: (cell, row) => appendText(cell, integerFormatter.format(row.wins)),
    },
    {
      key: "losses",
      label: "Lost",
      sortValue: (row) => row.losses,
      render: (cell, row) => appendText(cell, integerFormatter.format(row.losses)),
    },
    {
      key: "draws",
      label: "Drawn",
      sortValue: (row) => row.draws,
      render: (cell, row) => appendText(cell, integerFormatter.format(row.draws)),
    },
    {
      key: "winRate",
      label: "Win %",
      sortLabel: "win rate against this opponent",
      sortValue: (row) => row.winRate,
      render: (cell, row) => appendText(cell, formatRate(row.winRate)),
    },
    {
      key: "trend",
      label: "Trend",
      sortLabel: "recent form",
      sortValue: (row) => (row.trend ? row.trend.delta : null),
      render: (cell, row) => appendTrend(cell, row.trend),
    },
    {
      key: "lastMet",
      label: "Last met",
      sortValue: (row) => row.lastRaceIndex,
      render: (cell, row) => {
        const race = participants.races[row.lastRaceIndex];
        const link = document.createElement("a");
        link.href = `${RACE_ROOT}${race.r}`;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = formatRaceDate(race.d);
        cell.appendChild(link);
      },
    },
  ];
}

function renderPlayerView(playerIndex) {
  const player = participants.players[playerIndex];
  const available = playerModes(playerIndex);
  if (playerState.mode !== ALL_MODES && !available.includes(playerState.mode)) {
    playerState.mode = ALL_MODES;
  }

  document.querySelector("#player-name").textContent = player.name;
  document.querySelector("#player-subtitle").textContent = `How they match up · ${modeLabel(playerState.mode)}`;

  renderModeToggle(document.querySelector("#player-mode-toggle"), available, playerState.mode, (mode) => {
    playerState.mode = mode;
    playerState.sort = null;
    history.replaceState(null, "", playerHash(player.id, mode));
    renderPlayerView(playerIndex);
  });

  const matchups = computeHeadToHead(playerIndex, playerState.mode).map((matchup) => ({
    ...matchup,
    player: participants.players[matchup.opponent],
    winRate: winRate(matchup.wins, matchup.losses),
    trend: trendFor(matchup.sequence),
    rank: 0,
  }));
  matchups.sort((left, right) => right.meetings - left.meetings || left.player.name.localeCompare(right.player.name));
  matchups.forEach((matchup, index) => {
    matchup.rank = index + 1;
  });

  renderPlayerSummary(playerIndex, playerState.mode, matchups);

  const container = document.querySelector("#player-h2h-table");
  const visible = matchups.filter((matchup) => matchesQuery(matchup.player.name, playerState.query));
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "table-empty";
    empty.textContent = matchups.length
      ? "No opponents match that search."
      : "No recorded opponents for this mode.";
    container.replaceChildren(empty);
  } else {
    const columns = headToHeadColumns();
    renderSortableTable(container, columns, sortRows(visible, columns, playerState.sort), playerState, (column) => {
      playerState.sort = nextSort(playerState, column);
      renderPlayerView(playerIndex);
    });
  }

  announce(
    "#player-announcer",
    `${visible.length} of ${matchups.length} opponents shown for ${modeLabel(playerState.mode)}.`,
  );
}

function parseHash() {
  const match = /^#\/player\/([^/]+)(?:\/([^/]+))?$/.exec(window.location.hash);
  if (!match) {
    return null;
  }
  return {
    id: decodeURIComponent(match[1]),
    mode: match[2] ? decodeURIComponent(match[2]) : ALL_MODES,
  };
}

function focusHeading(selector) {
  window.scrollTo(0, 0);
  document.querySelector(selector).focus({ preventScroll: true });
}

function showPlayer(playerIndex, modeId, isInitial) {
  const wasPlayer = currentView && currentView.kind === "player";
  playerState.mode = modeId;
  if (!wasPlayer || currentView.index !== playerIndex) {
    playerState.sort = null;
    playerState.query = "";
    document.querySelector("#player-search").value = "";
  }
  document.body.classList.add("viewing-player");
  document.querySelector("#player-view").hidden = false;
  renderPlayerView(playerIndex);
  document.title = `${participants.players[playerIndex].name} · ${BASE_TITLE}`;
  if (!isInitial && (!wasPlayer || currentView.index !== playerIndex)) {
    focusHeading("#player-name");
  }
  currentView = { kind: "player", index: playerIndex };
}

function showLeaderboard(isInitial) {
  const wasPlayer = currentView && currentView.kind === "player";
  document.body.classList.remove("viewing-player");
  document.querySelector("#player-view").hidden = true;
  document.title = BASE_TITLE;
  if (!isInitial && wasPlayer) {
    focusHeading("#leaderboard-title");
  }
  currentView = { kind: "leaderboard" };
}

function route(isInitial) {
  if (!participants) {
    return;
  }
  const target = parseHash();
  const playerIndex = target ? playerIndexById.get(target.id) : undefined;
  if (target && playerIndex !== undefined) {
    const mode = participants.modes.includes(target.mode) ? target.mode : ALL_MODES;
    showPlayer(playerIndex, mode, isInitial);
    return;
  }
  showLeaderboard(isInitial);
}

function bindFilter(inputSelector, state, rerender) {
  document.querySelector(inputSelector).addEventListener("input", (event) => {
    state.query = event.target.value.trim().toLowerCase();
    rerender();
  });
}

async function loadParticipants() {
  const status = document.querySelector("#leaderboard-status");
  try {
    const response = await fetch("data/participants.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Racer data request failed with HTTP ${response.status}.`);
    }
    const data = await response.json();
    validateParticipants(data);
    participants = data;
    buildIndexes(data);
    recordCache = new Map();

    bindFilter("#leaderboard-search", leaderboardState, renderLeaderboard);
    bindFilter("#player-search", playerState, () => {
      if (currentView && currentView.kind === "player") {
        renderPlayerView(currentView.index);
      }
    });
    document.querySelector("#leaderboard-min-races").addEventListener("click", () => {
      leaderboardState.minRaces = leaderboardState.minRaces > 0 ? 0 : MIN_RACES_THRESHOLD;
      renderLeaderboard();
    });

    renderLeaderboard();
    status.hidden = true;
    document.querySelector("#leaderboard-controls").hidden = false;
    route(true);
    window.addEventListener("hashchange", () => route(false));
  } catch (error) {
    console.error(error);
    status.classList.add("load-status--error");
    status.textContent = "Racer statistics could not be loaded. Refresh the generated data and try again.";
  }
}

loadParticipants();
