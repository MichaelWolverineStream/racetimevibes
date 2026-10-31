const PARTICIPANT_SCHEMA_VERSION = 6;
const ALL_MODES = "all";
const DONE_STATUS = 0;
const TREND_WINDOW = 5;
const TREND_THRESHOLD = 0.05;
const MIN_RACES_THRESHOLD = 5;
const RECENT_DAYS = 30;
const DQ_STATUS = 2;
const DEFAULT_TIMEZONE = "America/New_York";
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
let badgeHolderCounts = new Map();
let recordCache = new Map();
let ratingRankCache = null;
let ratingRankDeltaCache = null;
let ratingDeltaCache = null;
let currentView = null;

const DEFAULT_LEADERBOARD_SORT = { key: "rating", direction: "desc" };
const leaderboardState = {
  mode: ALL_MODES,
  sort: DEFAULT_LEADERBOARD_SORT,
  query: "",
  minRaces: 0,
  recentOnly: false,
  followedOnly: false,
  range: { preset: "all", start: null, end: null },
};
const playerState = { mode: ALL_MODES, sort: null, query: "", heatmapMetric: "h2h" };

function validateParticipants(data) {
  if (!data || data.schema_version !== PARTICIPANT_SCHEMA_VERSION) {
    throw new Error("Unexpected racer data schema version.");
  }
  if (
    !Array.isArray(data.players) ||
    !Array.isArray(data.races) ||
    !Array.isArray(data.modes) ||
    !Array.isArray(data.leaderboards) ||
    !Array.isArray(data.presets)
  ) {
    throw new Error("Racer data is missing required collections.");
  }
  if (!data.players.length || !data.races.length) {
    throw new Error("Racer data is empty.");
  }
  if (
    !Array.isArray(data.badges) ||
    !data.badges.every((badge) => ["id", "label", "image", "still"].every((key) => typeof badge[key] === "string"))
  ) {
    throw new Error("Racer data contains an invalid badge definition.");
  }
  data.players.forEach((player) => {
    if (player.rating !== null && typeof player.rating !== "number") {
      throw new Error("Racer data contains an invalid rating.");
    }
    if (typeof player.rating === "number" && (typeof player.mu !== "number" || typeof player.sigma !== "number")) {
      throw new Error("Racer data contains an invalid rating.");
    }
  });
  data.races.forEach((race) => {
    if (
      !Array.isArray(race.e) ||
      !(race.p === null || (Number.isInteger(race.p) && race.p >= 0 && race.p < data.presets.length)) ||
      (race.t !== undefined && !Array.isArray(race.t)) ||
      !Number.isInteger(race.m) ||
      race.m < 0 ||
      race.m >= data.modes.length ||
      !Number.isInteger(race.h) ||
      race.h < 0 ||
      race.h > 23
    ) {
      throw new Error("Racer data contains an invalid race record.");
    }
    race.e.forEach((entry) => {
      if (!Array.isArray(entry) || entry.length < 5 || !Number.isInteger(entry[0]) || entry[0] < 0 || entry[0] >= data.players.length) {
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
  badgeHolderCounts = new Map();
  data.players.forEach((player) => {
    Object.keys(player.badges).forEach((badgeId) => badgeHolderCounts.set(badgeId, (badgeHolderCounts.get(badgeId) ?? 0) + 1));
  });
}

function modeIndexFor(modeId) {
  return modeId === ALL_MODES ? null : participants.modes.indexOf(modeId);
}

function modeLabel(modeId) {
  return modeId === ALL_MODES ? "All modes" : formatModeName(modeId);
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

function recordsForMode(modeId, range) {
  const cacheKey = `${modeId}|${range.start ?? ""}|${range.end ?? ""}`;
  if (recordCache.has(cacheKey)) {
    return recordCache.get(cacheKey);
  }
  const modeIndex = modeIndexFor(modeId);
  const records = participants.players.map(() => ({ wins: 0, losses: 0, draws: 0 }));
  participants.races.forEach((race) => {
    if ((modeIndex !== null && race.m !== modeIndex) || !inRange(race, range)) {
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
  recordCache.set(cacheKey, records);
  return records;
}

// Mirrors _leaderboard() in scripts/participant_stats.py for a date-limited slice of races.
// Players are stored sorted by casefolded name, so the index is the name tie-break.
function computeBoardRows(modeIndex, range) {
  const totals = new Map();
  participants.races.forEach((race) => {
    if ((modeIndex !== null && race.m !== modeIndex) || !inRange(race, range)) {
      return;
    }
    race.e.forEach((entry) => {
      const [playerIndex, place, status, seconds] = entry;
      let row = totals.get(playerIndex);
      if (!row) {
        row = { p: playerIndex, races: 0, first: 0, second: 0, third: 0, dnf: 0, dq: 0, times: [] };
        totals.set(playerIndex, row);
      }
      row.races += 1;
      if (isFinisher(entry)) {
        if (seconds !== null) {
          row.times.push(seconds);
        }
        if (place === 1) {
          row.first += 1;
        } else if (place === 2) {
          row.second += 1;
        } else if (place === 3) {
          row.third += 1;
        }
      } else if (status === DQ_STATUS) {
        row.dq += 1;
      } else {
        row.dnf += 1;
      }
    });
  });
  return [...totals.values()]
    .map(({ times, ...row }) => ({
      ...row,
      median_seconds: times.length ? median(times) : null,
    }))
    .sort(
      (left, right) =>
        right.first - left.first ||
        right.second - left.second ||
        right.third - left.third ||
        right.races - left.races ||
        left.p - right.p,
    );
}

function playerIndexFor(userId) {
  return playerIndexById.get(userId);
}

// Competition ranks: one more than the number of racers rated strictly higher, so ties share a rank.
function competitionRanks(ratings) {
  const firstRank = new Map();
  [...ratings.values()]
    .sort((left, right) => right - left)
    .forEach((rating, index) => {
      if (!firstRank.has(rating)) {
        firstRank.set(rating, index + 1);
      }
    });
  return new Map([...ratings].map(([playerIndex, rating]) => [playerIndex, firstRank.get(rating)]));
}

// Undefined for an unrated racer.
function ratingRank(playerIndex) {
  if (!ratingRankCache) {
    const ratings = new Map();
    participants.players.forEach((player, index) => {
      if (typeof player.rating === "number") {
        ratings.set(index, player.rating);
      }
    });
    ratingRankCache = competitionRanks(ratings);
  }
  return ratingRankCache.get(playerIndex);
}

// Positive when the racer climbed the overall rating ranks over the last 7 days; "new" if they had not raced then.
// A rating only moves when its racer races, so the rating on a past date is the one after their last race by then.
function ratingRankDeltas() {
  if (ratingRankDeltaCache) {
    return ratingRankDeltaCache;
  }
  const cutoff = shiftIsoDate(todayInZone(), -7);
  const ratingsBefore = new Map();
  participants.races.forEach((race) => {
    if (race.d > cutoff) {
      return;
    }
    race.e.forEach((entry) => {
      if (typeof entry[4] === "number") {
        ratingsBefore.set(entry[0], entry[4]);
      }
    });
  });
  const ranksBefore = competitionRanks(ratingsBefore);
  const deltas = new Map();
  participants.players.forEach((player, playerIndex) => {
    const rank = ratingRank(playerIndex);
    if (rank === undefined) {
      return;
    }
    const previous = ranksBefore.get(playerIndex);
    if (previous === undefined) {
      deltas.set(playerIndex, "new");
    } else if (previous !== rank) {
      deltas.set(playerIndex, previous - rank);
    }
  });
  ratingRankDeltaCache = deltas;
  return deltas;
}

// Rating change for one entry; a first race counts from the starting rating.
function ratingDelta(raceIndex, entryIndex) {
  if (!ratingDeltaCache) {
    const last = new Map();
    ratingDeltaCache = participants.races.map((race) =>
      race.e.map((entry) => {
        const previous = last.get(entry[0]);
        last.set(entry[0], entry[4]);
        return entry[4] - (previous === undefined ? participants.rating.base : previous);
      }),
    );
  }
  return ratingDeltaCache[raceIndex][entryIndex];
}

function lastRaceDate(playerIndex, modeIndex) {
  const raceIndexes = raceIndexesByPlayer[playerIndex];
  for (let index = raceIndexes.length - 1; index >= 0; index -= 1) {
    const race = participants.races[raceIndexes[index]];
    if (modeIndex === null || race.m === modeIndex) {
      return race.d;
    }
  }
  return null;
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

function hasRating(player) {
  return typeof player.rating === "number" && typeof player.mu === "number" && typeof player.sigma === "number";
}

// Chance that `subject` beats `opponent` next time, from their current ratings.
function winChance(subject, opponent) {
  if (!hasRating(subject) || !hasRating(opponent)) {
    return null;
  }
  const { beta } = participants.rating;
  const spread = Math.sqrt(subject.sigma ** 2 + opponent.sigma ** 2 + 2 * beta ** 2);
  return 1 / (1 + Math.exp(-(subject.mu - opponent.mu) / spread));
}

function ratingText(player) {
  return player.rating === null ? "--" : String(player.rating);
}

function ratingTooltip() {
  return `Ladder points \u00B7 new racers start at ${participants.rating.base} \u00B7 winning never costs points \u00B7 all modes`;
}

function formatRaceDate(isoDate) {
  return dateFormatter.format(new Date(`${isoDate}T00:00:00Z`));
}

// Race dates are local to the data's timezone, so "today" must be too.
function todayInZone() {
  const timeZone = (participants.timezone && participants.timezone.name) || DEFAULT_TIMEZONE;
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

function shiftIsoDate(isoDate, days) {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

// The last `days` calendar days, counting today.
function daysAgoCutoff(days) {
  return shiftIsoDate(todayInZone(), -(days - 1));
}

function inRange(race, range) {
  return (!range.start || race.d >= range.start) && (!range.end || race.d <= range.end);
}

function isRangeActive(range) {
  return Boolean(range.start || range.end);
}

function rangeLabel(range) {
  if (range.start && range.end) {
    return `races from ${formatRaceDate(range.start)} to ${formatRaceDate(range.end)}`;
  }
  return range.start ? `races from ${formatRaceDate(range.start)}` : `races through ${formatRaceDate(range.end)}`;
}

function playerHash(playerId, modeId) {
  const suffix = modeId === ALL_MODES ? "" : `/${encodeURIComponent(modeId)}`;
  return `#/player/${encodeURIComponent(playerId)}${suffix}`;
}

function appendRankDelta(cell, rank, delta) {
  const arrow = document.createElement("span");
  if (delta === "new") {
    arrow.className = "rank-new";
    arrow.textContent = "NEW";
    arrow.title = "New in the last 7 days";
  } else {
    arrow.className = `trend rank-delta trend--${delta > 0 ? "up" : "down"}`;
    arrow.textContent = `${delta > 0 ? "\u25B2" : "\u25BC"}${Math.abs(delta)}`;
    arrow.title = `Rating rank 7 days ago: #${rank + delta}`;
  }
  cell.appendChild(arrow);
}

// `rowNumber` is the row's position as displayed; `rankDelta` is the racer's rating-rank movement.
function appendRacerLink(cell, player, modeId, rowNumber, rankDelta) {
  if (rowNumber !== null) {
    const badge = document.createElement("span");
    badge.className = rowNumber <= 3 ? `rank-badge rank-badge--podium rank-badge--${rowNumber}` : "rank-badge";
    badge.textContent = integerFormatter.format(rowNumber);
    cell.appendChild(badge);
    if (rankDelta !== undefined) {
      appendRankDelta(cell, ratingRank(playerIndexFor(player.id)), rankDelta);
    }
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

function appendMedal(cell, value, medal) {
  cell.textContent = integerFormatter.format(value);
  if (value > 0) {
    cell.classList.add(`medal-count--${medal}`);
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
    if (column.tooltip) {
      bindTooltip(button, column.tooltip);
    }
    cell.appendChild(button);
    headRow.appendChild(cell);
  });

  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  rows.forEach((row, rowIndex) => {
    const bodyRow = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("td");
      cell.classList.add(`align-${column.align || "right"}`);
      column.render(cell, row, rowIndex);
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
  const { sort } = leaderboardState;
  // Rank movement is in overall rating rank, so it only reads against the row order when sorted by rating.
  const showRankMovement = sort.key === DEFAULT_LEADERBOARD_SORT.key && sort.direction === DEFAULT_LEADERBOARD_SORT.direction;
  return [
    {
      key: "name",
      label: "Racer",
      sortLabel: "racer name",
      align: "left",
      defaultDirection: "asc",
      sortValue: (row) => row.player.name.toLowerCase(),
      render: (cell, row, rowIndex) => {
        const delta = showRankMovement ? ratingRankDeltas().get(row.p) : undefined;
        appendRacerLink(cell, row.player, leaderboardState.mode, rowIndex + 1, delta);
        if (typeof UI !== "undefined") {
          const star = UI.followStar(row.p);
          star.classList.add("follow-star--row");
          cell.appendChild(star);
        }
      },
    },
    {
      key: "rating",
      label: "Rating",
      sortLabel: "ladder rating",
      tooltip: "Ladder rating · all modes, all time",
      sortValue: (row) => row.player.rating,
      render: (cell, row) => {
        const text = document.createElement("span");
        text.className = "rating-value";
        text.textContent = ratingText(row.player);
        if (row.player.rating !== null) {
          bindTooltip(text, ratingTooltip());
        }
        cell.appendChild(text);
      },
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
      render: (cell, row) => appendMedal(cell, row.first, "first"),
    },
    {
      key: "second",
      label: "2nd",
      sortLabel: "second places",
      sortValue: (row) => row.second,
      render: (cell, row) => appendMedal(cell, row.second, "second"),
    },
    {
      key: "third",
      label: "3rd",
      sortLabel: "third places",
      sortValue: (row) => row.third,
      render: (cell, row) => appendMedal(cell, row.third, "third"),
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
      sortLabel: "share of races won",
      sortValue: (row) => row.raceWinRate,
      render: (cell, row) => appendText(cell, formatRate(row.raceWinRate)),
    },
    {
      key: "headToHead",
      label: "H2H win %",
      sortLabel: "head-to-head win rate",
      sortValue: (row) => row.headToHeadRate,
      render: (cell, row) => appendText(cell, formatRate(row.headToHeadRate)),
    },
    {
      key: "badges",
      label: "Badges",
      sortLabel: "badges earned",
      tooltip: "Badges earned \u00B7 all modes, all time",
      sortValue: (row) => row.badges,
      render: (cell, row) => appendText(cell, integerFormatter.format(row.badges)),
    },
    {
      key: "median",
      label: "Median time",
      sortLabel: "median finish time",
      defaultDirection: "asc",
      sortValue: (row) => row.medianSeconds,
      render: (cell, row) => appendText(cell, formatDuration(row.medianSeconds)),
    },
  ];
}

// Rows in rating order, highest first, unrated last; the players array is name-sorted, so its index breaks ties.
function leaderboardRows(modeId, range) {
  const modeIndex = modeIndexFor(modeId);
  let boardRows;
  if (isRangeActive(range)) {
    boardRows = computeBoardRows(modeIndex, range);
  } else {
    const board = participants.leaderboards.find((item) => item.mode === modeId);
    if (!board) {
      return [];
    }
    boardRows = board.rows;
  }
  const records = recordsForMode(modeId, range);
  const rows = boardRows.map((row) => {
    const record = records[row.p];
    const player = participants.players[row.p];
    return {
      rank: 0,
      p: row.p,
      player,
      lastRaceDate: lastRaceDate(row.p, modeIndex),
      races: row.races,
      first: row.first,
      second: row.second,
      third: row.third,
      unfinished: row.dnf + row.dq,
      badges: Object.keys(player.badges).length,
      medianSeconds: row.median_seconds,
      raceWinRate: row.first / row.races,
      headToHeadRate: winRate(record.wins, record.losses),
    };
  });
  const ratingOf = (row) => row.player.rating ?? -Infinity;
  rows.sort((left, right) => ratingOf(right) - ratingOf(left) || left.p - right.p);
  rows.forEach((row, index) => {
    row.rank = index + 1;
  });
  return rows;
}

function renderLeaderboard({ fromSearch = false } = {}) {
  const container = document.querySelector("#leaderboard-table");
  renderModeToggle(
    document.querySelector("#leaderboard-mode-toggle"),
    participants.modes,
    leaderboardState.mode,
    (mode) => {
      leaderboardState.mode = mode;
      leaderboardState.sort = DEFAULT_LEADERBOARD_SORT;
      renderLeaderboard();
    },
  );

  const { range } = leaderboardState;
  const columns = leaderboardColumns();
  const all = leaderboardRows(leaderboardState.mode, range);
  const recentCutoff = daysAgoCutoff(RECENT_DAYS);
  const visible = all.filter(
    (row) =>
      row.races >= leaderboardState.minRaces &&
      (!leaderboardState.recentOnly || row.lastRaceDate >= recentCutoff) &&
      (!leaderboardState.followedOnly || Following.has(row.player.id)) &&
      matchesQuery(row.player.name, leaderboardState.query),
  );
  document
    .querySelector("#leaderboard-min-races")
    .setAttribute("aria-pressed", String(leaderboardState.minRaces > 0));
  document
    .querySelector("#leaderboard-recent")
    .setAttribute("aria-pressed", String(leaderboardState.recentOnly));
  document
    .querySelector("#leaderboard-followed")
    .setAttribute("aria-pressed", String(leaderboardState.followedOnly));
  document.querySelectorAll("#leaderboard-range-presets [data-range]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.range === range.preset));
  });
  document.querySelector("#leaderboard-from").value = range.start || "";
  document.querySelector("#leaderboard-to").value = range.end || "";
  const renderFinishTimes = fromSearch ? renderLeaderboardFinishTimesSoon : renderLeaderboardFinishTimes;
  renderFinishTimes(visible, leaderboardState.mode, range);

  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "table-empty";
    empty.textContent = emptyLeaderboardText(all.length > 0);
    container.replaceChildren(empty);
  } else {
    renderSortableTable(container, columns, sortRows(visible, columns, leaderboardState.sort), leaderboardState, (column) => {
      leaderboardState.sort = nextSort(leaderboardState, column);
      renderLeaderboard();
    });
  }

  const sortLabel = leaderboardState.sort
    ? `Sorted by ${columns.find((item) => item.key === leaderboardState.sort.key).sortLabel || ""}, ${leaderboardState.sort.direction === "asc" ? "ascending" : "descending"}. `
    : "";
  const filterLabels = [];
  if (isRangeActive(range)) {
    filterLabels.push(rangeLabel(range));
  }
  if (leaderboardState.minRaces > 0) {
    filterLabels.push(`${MIN_RACES_THRESHOLD}+ races only`);
  }
  if (leaderboardState.recentOnly) {
    filterLabels.push(`raced in the last ${RECENT_DAYS} days`);
  }
  if (leaderboardState.followedOnly) {
    filterLabels.push("followed racers only");
  }
  const filterLabel = filterLabels.map((label) => `, ${label}`).join("");
  announce("#leaderboard-announcer", `${sortLabel}${visible.length} of ${all.length} racers shown for ${modeLabel(leaderboardState.mode)}${filterLabel}.`);
}

function emptyLeaderboardText(hasRaces) {
  if (!hasRaces) {
    return "No races in this mode during the selected dates.";
  }
  if (leaderboardState.followedOnly && !Following.ids().length) {
    return "Star a racer to follow them, then they show up here.";
  }
  return "No racers match these filters.";
}

function clampIsoDate(isoDate, min, max) {
  if (isoDate < min) {
    return min;
  }
  return isoDate > max ? max : isoDate;
}

function presetRange(preset, firstDate, lastDate) {
  if (preset === "all") {
    return { preset, start: null, end: null };
  }
  const today = todayInZone();
  const start = preset === "year" ? `${today.slice(0, 4)}-01-01` : daysAgoCutoff(Number(preset));
  return {
    preset,
    start: clampIsoDate(start, firstDate, lastDate),
    end: clampIsoDate(today, firstDate, lastDate),
  };
}

function bindRangeControls() {
  const firstDate = participants.races[0].d;
  const lastDate = participants.races.at(-1).d;
  const fromInput = document.querySelector("#leaderboard-from");
  const toInput = document.querySelector("#leaderboard-to");
  [fromInput, toInput].forEach((input) => {
    input.min = firstDate;
    input.max = lastDate;
    input.addEventListener("change", () => {
      let start = fromInput.value || null;
      let end = toInput.value || null;
      // Keep the date just picked and move the other bound to meet it.
      if (start && end && start > end) {
        if (input === fromInput) {
          end = start;
        } else {
          start = end;
        }
      }
      leaderboardState.range = { preset: start || end ? null : "all", start, end };
      renderLeaderboard();
    });
  });
  document.querySelectorAll("#leaderboard-range-presets [data-range]").forEach((button) => {
    button.addEventListener("click", () => {
      leaderboardState.range = presetRange(button.dataset.range, firstDate, lastDate);
      renderLeaderboard();
    });
  });
}

function playerModes(playerIndex) {
  const seen = new Set();
  raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
    seen.add(participants.modes[participants.races[raceIndex].m]);
  });
  return participants.modes.filter((mode) => seen.has(mode));
}

function headToHeadColumns(playerIndex) {
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
      key: "winChance",
      label: "Win chance",
      sortLabel: "chance of beating this opponent next time",
      tooltip: "From current ratings",
      sortValue: (row) => winChance(participants.players[playerIndex], row.player),
      render: (cell, row) => appendText(cell, formatRate(winChance(participants.players[playerIndex], row.player))),
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

  renderModeToggle(document.querySelector("#player-mode-toggle"), available, playerState.mode, (mode) => {
    playerState.mode = mode;
    playerState.sort = null;
    history.replaceState(null, "", playerHash(player.id, mode));
    renderPlayerView(playerIndex);
    renderPlayerCharts(playerIndex);
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
    const columns = headToHeadColumns(playerIndex);
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

// A failing feature must never break the board or the racer page.
function runFeature(name, run) {
  try {
    run();
  } catch (error) {
    console.error(`${name} failed`, error);
  }
}

// The extras cover every mode, so they only redraw for a different racer.
function renderRacerExtras(playerIndex) {
  if (typeof RacerExtras !== "undefined") {
    runFeature("RacerExtras.render", () => RacerExtras.render(playerIndex));
  }
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
  renderPlayerCharts(playerIndex);
  document.title = `${participants.players[playerIndex].name} · ${BASE_TITLE}`;
  if (!isInitial && (!wasPlayer || currentView.index !== playerIndex)) {
    focusHeading("#player-name");
  }
  currentView = { kind: "player", index: playerIndex };
  renderRacerExtras(playerIndex);
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
    ratingRankCache = null;
    ratingRankDeltaCache = null;
    ratingDeltaCache = null;

    bindFilter("#leaderboard-search", leaderboardState, () => renderLeaderboard({ fromSearch: true }));
    bindFilter("#player-search", playerState, () => {
      if (currentView && currentView.kind === "player") {
        renderPlayerView(currentView.index);
      }
    });
    document.querySelector("#leaderboard-min-races").addEventListener("click", () => {
      leaderboardState.minRaces = leaderboardState.minRaces > 0 ? 0 : MIN_RACES_THRESHOLD;
      renderLeaderboard();
    });
    document.querySelector("#leaderboard-recent").addEventListener("click", () => {
      leaderboardState.recentOnly = !leaderboardState.recentOnly;
      renderLeaderboard();
    });
    document.querySelector("#leaderboard-followed").addEventListener("click", () => {
      leaderboardState.followedOnly = !leaderboardState.followedOnly;
      renderLeaderboard();
    });
    // Unstarring a racer while the Followed filter is on drops their row straight away.
    document.addEventListener(FOLLOWING_EVENT, () => {
      if (leaderboardState.followedOnly) {
        renderLeaderboard();
      }
    });
    bindRangeControls();
    document.querySelectorAll("#player-heatmap-metric [data-metric]").forEach((button) => {
      button.addEventListener("click", () => {
        playerState.heatmapMetric = button.dataset.metric;
        if (currentView && currentView.kind === "player") {
          renderPlayerHeatmap(currentView.index);
        }
      });
    });

    renderLeaderboard();
    status.hidden = true;
    document.querySelector("#finish-times").hidden = false;
    document.querySelector("#leaderboard-controls").hidden = false;
    route(true);
    window.addEventListener("hashchange", () => route(false));
    if (typeof Feed !== "undefined") {
      runFeature("Feed.init", () => Feed.init());
    }
    if (typeof Records !== "undefined") {
      runFeature("Records.init", () => Records.init());
    }
    if (typeof EasterEggs !== "undefined") {
      runFeature("EasterEggs.init", () => EasterEggs.init());
    }
  } catch (error) {
    console.error(error);
    status.classList.add("load-status--error");
    status.textContent = "Racer statistics could not be loaded. Refresh the generated data and try again.";
  }
}

loadParticipants();
