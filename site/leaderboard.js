const PARTICIPANT_SCHEMA_VERSION = 15;
// Medal names, best first. Must match MEDALS in scripts/highlights.py and the .medal-count--* and .rank-badge--* classes in styles.css.
const MEDALS = ["diamond", "gold", "silver", "bronze"];
const MEDAL_RULE_TOOLTIP =
  "Medals by field size: 2\u20133 racers gold \u00B7 4\u20135 gold, silver \u00B7 6\u20139 gold, silver, bronze \u00B7 10+ diamond, gold, silver, bronze";
const ALL_MODES = "all";
// Races with a missing or malformed mode prefix; a bucket, not a category.
const UNKNOWN_MODE = "unknown";
const DONE_STATUS = 0;
const TREND_WINDOW = 5;
const TREND_THRESHOLD = 0.05;
const MIN_RACES_THRESHOLD = 5;
const RECENT_DAYS = 30;
const DQ_STATUS = 2;
const DEFAULT_TIMEZONE = "America/New_York";
const RACE_ROOT = "https://racetime.gg/";
const BASE_TITLE = document.title;
const SEASON_ALL_VALUE = "all";

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
let ratingRankCache = new Map();
let ratingRankDeltaCache = new Map();
let seasonStandingsCache = new Map();
let seasonModeRatingDeltaCache = null;
let currentView = null;
// window.scrollY when the board was last left for a racer page; null until then.
let boardScrollY = null;

const DEFAULT_LEADERBOARD_SORT = { key: "rating", direction: "desc" };
const leaderboardState = {
  mode: ALL_MODES,
  sort: DEFAULT_LEADERBOARD_SORT,
  query: "",
  minRaces: 0,
  recentOnly: false,
  followedOnly: false,
  // A season number, or null for all seasons; loadParticipants sets the current season.
  season: null,
};
const playerState = { mode: ALL_MODES, sort: null, query: "", presetSort: null };

// medal_tiers is [[min field size, [medal, ...]], ...], largest field first.
function validMedalTiers(tiers) {
  return (
    Array.isArray(tiers) &&
    tiers.length > 0 &&
    tiers.every(
      (tier, index) =>
        Array.isArray(tier) &&
        tier.length === 2 &&
        Number.isInteger(tier[0]) &&
        tier[0] > 0 &&
        Array.isArray(tier[1]) &&
        tier[1].length > 0 &&
        tier[1].every((medal) => MEDALS.includes(medal)) &&
        (index === 0 || tiers[index - 1][0] > tier[0]),
    )
  );
}

// The medal a finisher's place earns in a field of `field` entrants, or null. Callers check the finisher status first.
function medalFor(place, field) {
  const tier = participants.medal_tiers.find(([minField]) => field >= minField);
  return tier ? (tier[1][place - 1] ?? null) : null;
}

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
  if (!validMedalTiers(data.medal_tiers)) {
    throw new Error("Racer data contains an invalid medal rule.");
  }
  if (
    !Array.isArray(data.badges) ||
    !data.badges.every((badge) => ["id", "label", "image", "still"].every((key) => typeof badge[key] === "string"))
  ) {
    throw new Error("Racer data contains an invalid badge definition.");
  }
  if (
    !Array.isArray(data.seasons) ||
    !data.seasons.every((season, index) => (
      season !== null &&
      typeof season === "object" &&
      Number.isInteger(season.n) &&
      (index === 0 || season.n > data.seasons[index - 1].n) &&
      typeof season.first === "string" &&
      typeof season.last === "string" &&
      typeof season.ended === "boolean"
    ))
  ) {
    throw new Error("Racer data contains an invalid season.");
  }
  const seasonNumbers = new Set(data.seasons.map((season) => season.n));
  const isText = (value) => typeof value === "string" && value !== "";
  const names = data.season_names;
  if (
    names === null ||
    typeof names !== "object" ||
    Array.isArray(names) ||
    !Object.entries(names).every(([key, name]) => (
      Number.isInteger(Number(key)) &&
      Number(key) > 0 &&
      String(Number(key)) === key &&
      name !== null &&
      typeof name === "object" &&
      ((name.kind === "retro" && isText(name.text) && isText(name.full)) ||
        (name.kind === "champion" && isText(name.text)))
    ))
  ) {
    throw new Error("Racer data contains an invalid season name.");
  }
  if (
    !Array.isArray(data.upcoming) ||
    !data.upcoming.every((row) => (
      Array.isArray(row) &&
      row.length === 4 &&
      Number.isInteger(row[0]) &&
      isText(row[1]) &&
      (row[2] === null || (Number.isInteger(row[2]) && row[2] >= 0 && row[2] < data.modes.length)) &&
      (row[3] === null || Number.isInteger(row[3]))
    ))
  ) {
    throw new Error("Racer data contains an invalid upcoming race.");
  }
  data.players.forEach((player) => {
    if (player.rating !== null && typeof player.rating !== "number") {
      throw new Error("Racer data contains an invalid rating.");
    }
    if (typeof player.rating === "number" && (typeof player.mu !== "number" || typeof player.sigma !== "number")) {
      throw new Error("Racer data contains an invalid rating.");
    }
    const modeRatings = player.mode_ratings;
    if (modeRatings === null || typeof modeRatings !== "object" || Array.isArray(modeRatings)) {
      throw new Error("Racer data contains an invalid mode rating.");
    }
    Object.entries(modeRatings).forEach(([modeIndex, rating]) => {
      const index = Number(modeIndex);
      if (
        !Number.isInteger(index) ||
        String(index) !== modeIndex ||
        index < 0 ||
        index >= data.modes.length ||
        rating === null ||
        typeof rating !== "object" ||
        !Number.isInteger(rating.rating) ||
        typeof rating.mu !== "number" ||
        typeof rating.sigma !== "number" ||
        !Number.isInteger(rating.finishes)
      ) {
        throw new Error("Racer data contains an invalid mode rating.");
      }
    });
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
      race.h > 23 ||
      !(race.s === undefined || (Number.isInteger(race.s) && seasonNumbers.has(race.s)))
    ) {
      throw new Error("Racer data contains an invalid race record.");
    }
    race.e.forEach((entry) => {
      if (!Array.isArray(entry) || entry.length < 8 || !Number.isInteger(entry[0]) || entry[0] < 0 || entry[0] >= data.players.length) {
        throw new Error("Racer data contains an out-of-range player index.");
      }
      if (typeof entry[5] !== "number") {
        throw new Error("Racer data contains an invalid mode rating after a race.");
      }
      const seasonRatings = [entry[6], entry[7]];
      const valid = race.s === undefined ? seasonRatings.every((value) => value === null) : seasonRatings.every(Number.isInteger);
      if (!valid) {
        throw new Error("Racer data contains an invalid season rating after a race.");
      }
    });
  });
  const modes = data.leaderboards.map((board) => board.mode);
  if (modes[0] !== ALL_MODES) {
    throw new Error("Racer data is missing the combined leaderboard.");
  }
  if (
    !Array.isArray(data.avatar_changes) ||
    !data.avatar_changes.every((change) => (
      Array.isArray(change) &&
      Number.isInteger(change[0]) &&
      change[0] >= 0 &&
      change[0] < data.players.length &&
      typeof change[1] === "string" &&
      /^\d{4}-\d{2}-\d{2}$/.test(change[1])
    ))
  ) {
    throw new Error("Racer data contains an invalid avatar change.");
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

// Whether a race counts toward `season`; null is all time.
function inSeason(race, season) {
  return season === null || race.s === season;
}

// Win/loss/draw records in one mode (or all) over one season (or null for all time).
function recordsForMode(modeId, season) {
  const cacheKey = `${modeId}|${season ?? "all"}`;
  if (recordCache.has(cacheKey)) {
    return recordCache.get(cacheKey);
  }
  const modeIndex = modeIndexFor(modeId);
  const records = participants.players.map(() => ({ wins: 0, losses: 0, draws: 0 }));
  participants.races.forEach((race) => {
    if ((modeIndex !== null && race.m !== modeIndex) || !inSeason(race, season)) {
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

// Mirrors _leaderboard() in scripts/participant_stats.py for one season's races:
// wins count 1st place in any field; medals follow the field-size rule in medalFor.
// Players are stored sorted by casefolded name, so the index is the name tie-break.
function computeBoardRows(modeIndex, season) {
  const totals = new Map();
  participants.races.forEach((race) => {
    if ((modeIndex !== null && race.m !== modeIndex) || !inSeason(race, season)) {
      return;
    }
    race.e.forEach((entry) => {
      const [playerIndex, place, status, seconds] = entry;
      let row = totals.get(playerIndex);
      if (!row) {
        row = { p: playerIndex, races: 0, wins: 0, diamond: 0, gold: 0, silver: 0, bronze: 0, dnf: 0, dq: 0, times: [] };
        totals.set(playerIndex, row);
      }
      row.races += 1;
      if (isFinisher(entry)) {
        if (seconds !== null) {
          row.times.push(seconds);
        }
        if (place === 1) {
          row.wins += 1;
        }
        const medal = medalFor(place, race.n);
        if (medal) {
          row[medal] += 1;
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
        right.diamond - left.diamond ||
        right.gold - left.gold ||
        right.silver - left.silver ||
        right.bronze - left.bronze ||
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

// The current season is the newest one, the last in `seasons`; null when the data has none.
function currentSeason() {
  return participants.seasons.length ? participants.seasons.at(-1).n : null;
}

// The season's name from the data ({kind, text, full?}), or null when it has none.
function seasonName(season) {
  return participants.season_names[String(season)] ?? null;
}

// Visible text: "Season 10 · TR", "Season 11 · Panda", or "Season 12" when unnamed.
function seasonLabel(season) {
  const name = seasonName(season);
  return name ? `Season ${season} \u00B7 ${name.text}` : `Season ${season}`;
}

// Tooltip text: retro names spelled out ("Season 10 · Turtle Rock"), otherwise the same as seasonLabel.
function seasonFullLabel(season) {
  const name = seasonName(season);
  return name?.kind === "retro" ? `Season ${season} \u00B7 ${name.full}` : seasonLabel(season);
}

// A season's title: "The Season of the Panda", the retro short name, or "Season 12" when unnamed.
function seasonTitle(season) {
  const name = seasonName(season);
  if (!name) {
    return `Season ${season}`;
  }
  return name.kind === "champion" ? `The Season of the ${name.text}` : name.text;
}

// Players with competition rank 1 in a rating track, by player index; `season` null is all time.
function ratingLeaders(modeId = ALL_MODES, season = null) {
  return [...ratingRanks(modeId, season)]
    .filter(([, rank]) => rank === 1)
    .map(([playerIndex]) => playerIndex)
    .sort((a, b) => a - b);
}

// The overall leaders of an ended season; none while it is running or unknown.
function seasonChampions(season) {
  return participants.seasons.some((item) => item.n === season && item.ended) ? ratingLeaders(ALL_MODES, season) : [];
}

// The previous season's champions, who named this season; none unless it has a champion name.
function seasonNamers(season) {
  return seasonName(season)?.kind === "champion" ? seasonChampions(season - 1) : [];
}

// Every racer's standing in one season: the rating after their last race there, overall and per mode index,
// with their finishes. Season ratings carry no mu or sigma.
function seasonStandings(season) {
  if (!seasonStandingsCache.has(season)) {
    const standings = { overall: new Map(), modes: new Map() };
    const record = (standing, playerIndex, rating, entry) => {
      const finishes = (standing.get(playerIndex)?.finishes ?? 0) + (isFinisher(entry) ? 1 : 0);
      standing.set(playerIndex, { rating, finishes });
    };
    participants.races.forEach((race) => {
      if (race.s !== season) {
        return;
      }
      if (!standings.modes.has(race.m)) {
        standings.modes.set(race.m, new Map());
      }
      race.e.forEach((entry) => {
        record(standings.overall, entry[0], entry[6], entry);
        record(standings.modes.get(race.m), entry[0], entry[7], entry);
      });
    });
    seasonStandingsCache.set(season, standings);
  }
  return seasonStandingsCache.get(season);
}

function seasonStanding(season, modeId) {
  const standings = seasonStandings(season);
  return modeId === ALL_MODES ? standings.overall : (standings.modes.get(modeIndexFor(modeId)) ?? new Map());
}

// The racer's rating in the overall ladder or in one mode, all time or in one season; null when unrated in scope.
function ratingFor(player, modeId = ALL_MODES, season = null) {
  if (season !== null) {
    return seasonStanding(season, modeId).get(playerIndexFor(player.id)) ?? null;
  }
  if (modeId === ALL_MODES) {
    return player.rating === null ? null : { rating: player.rating, mu: player.mu, sigma: player.sigma, finishes: player.finishes };
  }
  return player.mode_ratings[modeIndexFor(modeId)] ?? null;
}

// Competition ranks of everyone rated overall or in one mode; its size is how many are rated.
function ratingRanks(modeId = ALL_MODES, season = null) {
  const cacheKey = `${season ?? "all"}|${modeId}`;
  if (!ratingRankCache.has(cacheKey)) {
    const ratings = new Map();
    participants.players.forEach((player, index) => {
      const rating = ratingFor(player, modeId, season);
      if (rating) {
        ratings.set(index, rating.rating);
      }
    });
    ratingRankCache.set(cacheKey, competitionRanks(ratings));
  }
  return ratingRankCache.get(cacheKey);
}

// Undefined for a racer unrated in scope.
function ratingRank(playerIndex, modeId = ALL_MODES, season = null) {
  return ratingRanks(modeId, season).get(playerIndex);
}

// The scopes the top-rated banner crowns and the featured racer avoids: the overall ladder and every named mode.
function ratingCategories() {
  return [ALL_MODES, ...participants.modes.filter((modeId) => modeId !== UNKNOWN_MODE)];
}

// Competition ranks as they stood at the end of `lastDate`, overall or in one mode, all time or in one season.
// A rating only moves when its racer races, so the rating on a past date is the one after their last race by then.
function ratingRanksThrough(modeId, lastDate, season = null) {
  const modeIndex = modeIndexFor(modeId);
  const ratingIndex = (season === null ? 4 : 6) + (modeIndex === null ? 0 : 1);
  const ratings = new Map();
  participants.races.forEach((race) => {
    if (race.d > lastDate || (modeIndex !== null && race.m !== modeIndex) || !inSeason(race, season)) {
      return;
    }
    race.e.forEach((entry) => {
      if (typeof entry[ratingIndex] === "number") {
        ratings.set(entry[0], entry[ratingIndex]);
      }
    });
  });
  return competitionRanks(ratings);
}

// Positive when the racer climbed the rating ranks, overall or in one mode, over the last 7 days; "new" if they had no rating then.
function ratingRankDeltas(modeId = ALL_MODES, season = null) {
  const cacheKey = `${season ?? "all"}|${modeId}`;
  if (ratingRankDeltaCache.has(cacheKey)) {
    return ratingRankDeltaCache.get(cacheKey);
  }
  const ranksBefore = ratingRanksThrough(modeId, shiftIsoDate(todayInZone(), -7), season);
  const deltas = new Map();
  participants.players.forEach((player, playerIndex) => {
    const rank = ratingRank(playerIndex, modeId, season);
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
  ratingRankDeltaCache.set(cacheKey, deltas);
  return deltas;
}

// Change in the entry's season rating in the race's own mode; a racer's first race of a mode in a season counts from the starting rating.
// Null for a race outside every season.
function seasonModeRatingDelta(raceIndex, entryIndex) {
  if (!seasonModeRatingDeltaCache) {
    const last = new Map();
    seasonModeRatingDeltaCache = participants.races.map((race) =>
      race.e.map((entry) => {
        if (race.s === undefined) {
          return null;
        }
        const key = `${race.s}:${race.m}:${entry[0]}`;
        const previous = last.get(key);
        last.set(key, entry[7]);
        return entry[7] - (previous === undefined ? participants.rating.base : previous);
      }),
    );
  }
  return seasonModeRatingDeltaCache[raceIndex][entryIndex];
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

function ratingText(rating) {
  return rating === null ? "--" : String(rating.rating);
}

function ratingTooltip(modeId = ALL_MODES, season = null) {
  const scope = modeId === ALL_MODES ? "all modes" : `${modeLabel(modeId)} races only`;
  const period = season === null ? "" : ` \u00B7 ${seasonFullLabel(season)}`;
  return `Ladder points \u00B7 new racers start at ${participants.rating.base} \u00B7 winning never costs points \u00B7 ${scope}${period}`;
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

function playerHash(playerId, modeId) {
  const suffix = modeId === ALL_MODES ? "" : `/${encodeURIComponent(modeId)}`;
  return `#/player/${encodeURIComponent(playerId)}${suffix}`;
}

// Face Off with the red corner's racer and the blue corner's, either of which may be empty (null).
// An empty red corner with a blue one picked keeps its place as an empty segment.
function faceoffHash(playerIdA = null, playerIdB = null) {
  const parts = [playerIdA ?? "", playerIdB ?? ""].map((id) => encodeURIComponent(id));
  while (parts.length && parts.at(-1) === "") {
    parts.pop();
  }
  return parts.length ? `#/faceoff/${parts.join("/")}` : "#/faceoff";
}

function appendRankDelta(cell, rank, delta, season = null) {
  const arrow = document.createElement("span");
  if (delta === "new") {
    arrow.className = "rank-new";
    arrow.textContent = "NEW";
    bindTooltip(arrow, "New in the last 7 days");
  } else {
    arrow.className = `trend rank-delta trend--${delta > 0 ? "up" : "down"}`;
    arrow.textContent = `${delta > 0 ? "\u25B2" : "\u25BC"}${Math.abs(delta)}`;
    bindTooltip(arrow, `${season === null ? "Rating" : `${seasonFullLabel(season)} rating`} rank 7 days ago: #${rank + delta}`);
  }
  cell.appendChild(arrow);
}

// `rowNumber` is the row's position as displayed; `rankDelta` is the racer's rating-rank movement in `season` (null: all time).
function appendRacerLink(cell, player, modeId, rowNumber, rankDelta, season = null) {
  if (rowNumber !== null) {
    const badge = document.createElement("span");
    badge.className = rowNumber <= 3 ? `rank-badge rank-badge--podium rank-badge--${["gold", "silver", "bronze"][rowNumber - 1]}` : "rank-badge";
    badge.textContent = integerFormatter.format(rowNumber);
    cell.appendChild(badge);
    if (rankDelta !== undefined) {
      appendRankDelta(cell, ratingRank(playerIndexFor(player.id), modeId, season), rankDelta, season);
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
    // A column without a sortValue holds actions, not values, so its header is a plain label.
    if (!column.sortValue) {
      cell.textContent = column.label;
      headRow.appendChild(cell);
      return;
    }
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
  const { sort, season } = leaderboardState;
  // Rank movement is in the selected scope's rating rank, so it only reads against the row order when sorted by rating.
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
        const delta = showRankMovement ? ratingRankDeltas(leaderboardState.mode, season).get(row.p) : undefined;
        appendRacerLink(cell, row.player, leaderboardState.mode, rowIndex + 1, delta, season);
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
      tooltip: season === null
        ? (leaderboardState.mode === ALL_MODES
          ? "All-time rating · all modes"
          : `All-time rating · ${modeLabel(leaderboardState.mode)}`)
        : `${seasonFullLabel(season)} rating · ${leaderboardState.mode === ALL_MODES ? "all modes" : modeLabel(leaderboardState.mode)}`,
      sortValue: (row) => row.rating?.rating,
      render: (cell, row) => {
        const text = document.createElement("span");
        text.className = "rating-value";
        text.textContent = ratingText(row.rating);
        if (row.rating !== null) {
          bindTooltip(text, ratingTooltip(leaderboardState.mode, season));
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
      key: "diamond",
      label: "Diamond",
      sortLabel: "diamond medals",
      tooltip: MEDAL_RULE_TOOLTIP,
      sortValue: (row) => row.diamond,
      render: (cell, row) => appendMedal(cell, row.diamond, "diamond"),
    },
    {
      key: "gold",
      label: "Gold",
      sortLabel: "gold medals",
      tooltip: MEDAL_RULE_TOOLTIP,
      sortValue: (row) => row.gold,
      render: (cell, row) => appendMedal(cell, row.gold, "gold"),
    },
    {
      key: "silver",
      label: "Silver",
      sortLabel: "silver medals",
      tooltip: MEDAL_RULE_TOOLTIP,
      sortValue: (row) => row.silver,
      render: (cell, row) => appendMedal(cell, row.silver, "silver"),
    },
    {
      key: "bronze",
      label: "Bronze",
      sortLabel: "bronze medals",
      tooltip: MEDAL_RULE_TOOLTIP,
      sortValue: (row) => row.bronze,
      render: (cell, row) => appendMedal(cell, row.bronze, "bronze"),
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

// Rows in order of the selected scope's rating (overall, or in the mode), highest first, unrated last; the players array is name-sorted, so its index breaks ties.
function leaderboardRows(modeId, season) {
  const modeIndex = modeIndexFor(modeId);
  let boardRows;
  if (season !== null) {
    boardRows = computeBoardRows(modeIndex, season);
  } else {
    const board = participants.leaderboards.find((item) => item.mode === modeId);
    if (!board) {
      return [];
    }
    boardRows = board.rows;
  }
  const records = recordsForMode(modeId, season);
  const rows = boardRows.map((row) => {
    const record = records[row.p];
    const player = participants.players[row.p];
    return {
      rank: 0,
      p: row.p,
      player,
      rating: ratingFor(player, modeId, season),
      lastRaceDate: lastRaceDate(row.p, modeIndex),
      races: row.races,
      wins: row.wins,
      diamond: row.diamond,
      gold: row.gold,
      silver: row.silver,
      bronze: row.bronze,
      unfinished: row.dnf + row.dq,
      badges: Object.keys(player.badges).length,
      medianSeconds: row.median_seconds,
      raceWinRate: row.wins / row.races,
      headToHeadRate: winRate(record.wins, record.losses),
    };
  });
  const ratingOf = (row) => row.rating?.rating ?? -Infinity;
  rows.sort((left, right) => ratingOf(right) - ratingOf(left) || left.p - right.p);
  rows.forEach((row, index) => {
    row.rank = index + 1;
  });
  return rows;
}

function renderLeaderboard() {
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

  const { season } = leaderboardState;
  const columns = leaderboardColumns();
  const all = leaderboardRows(leaderboardState.mode, season);
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
  document.querySelector("#leaderboard-season").value = season === null ? SEASON_ALL_VALUE : String(season);

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
  filterLabels.push(season === null ? "all seasons" : seasonLabel(season));
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
    return leaderboardState.season === null
      ? "No races in this mode."
      : `No races in this mode during ${seasonLabel(leaderboardState.season)}.`;
  }
  if (leaderboardState.followedOnly && !Following.ids().length) {
    return "Star a racer to follow them, then they show up here.";
  }
  return "No racers match these filters.";
}

// Newest season first, then "All seasons"; with no seasons in the data there is nothing to choose, so the control stays hidden.
function bindSeasonControl() {
  const control = document.querySelector("#leaderboard-season-control");
  const select = document.querySelector("#leaderboard-season");
  const options = [...participants.seasons].reverse().map((season) => {
    const option = document.createElement("option");
    option.value = String(season.n);
    option.textContent = seasonLabel(season.n);
    option.title = seasonFullLabel(season.n);
    return option;
  });
  const all = document.createElement("option");
  all.value = SEASON_ALL_VALUE;
  all.textContent = "All seasons";
  select.replaceChildren(...options, all);
  control.hidden = participants.seasons.length === 0;
  select.addEventListener("change", () => {
    selectLeaderboardSeason(select.value === SEASON_ALL_VALUE ? null : Number(select.value));
  });
}

// Shows one season (null: all seasons) on the leaderboard with the default sort; the caller handles scrolling and focus.
function selectLeaderboardSeason(season) {
  leaderboardState.season = season;
  document.querySelector("#leaderboard-season").value = season === null ? SEASON_ALL_VALUE : String(season);
  leaderboardState.sort = DEFAULT_LEADERBOARD_SORT;
  renderLeaderboard();
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
      tooltip: "From current overall ratings",
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
    {
      key: "faceoff",
      label: "Face off",
      align: "center",
      render: (cell, row) => {
        const link = document.createElement("a");
        link.className = "faceoff-vs-link";
        link.href = faceoffHash(participants.players[playerIndex].id, row.player.id);
        link.textContent = "VS";
        link.setAttribute("aria-label", `Face off with ${row.player.name}`);
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
    playerState.presetSort = null;
    history.replaceState(null, "", playerHash(player.id, mode));
    renderPlayerView(playerIndex);
    renderPlayerCharts(playerIndex);
    renderRacerRating(playerIndex);
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

// The page the hash names: a racer page, Face Off, or null for the board and its section anchors.
function parseRoute() {
  const hash = window.location.hash;
  const player = /^#\/player\/([^/]+)(?:\/([^/]+))?$/.exec(hash);
  if (player) {
    return {
      kind: "player",
      id: decodeURIComponent(player[1]),
      mode: player[2] ? decodeURIComponent(player[2]) : ALL_MODES,
    };
  }
  const faceoff = /^#\/faceoff(?:\/([^/]*)(?:\/([^/]*))?)?$/.exec(hash);
  if (faceoff) {
    return {
      kind: "faceoff",
      ids: [faceoff[1], faceoff[2]].map((part) => (part ? decodeURIComponent(part) : null)),
    };
  }
  return null;
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

// The racer's Latest results cover every mode, so the list only redraws for a different racer.
function renderRacerResults(playerIndex) {
  if (typeof Feed !== "undefined") {
    runFeature("Feed.racerResults", () => Feed.racerResults(playerIndex));
  }
}

// The rating chart follows the racer page's mode picker, so it redraws for a different mode too.
function renderRacerRating(playerIndex) {
  if (typeof RacerExtras !== "undefined") {
    runFeature("RacerExtras.renderRating", () => RacerExtras.renderRating(playerIndex, playerState.mode));
  }
}

function showPlayer(playerIndex, modeId, isInitial) {
  const wasPlayer = currentView && currentView.kind === "player";
  playerState.mode = modeId;
  if (!wasPlayer || currentView.index !== playerIndex) {
    playerState.sort = null;
    playerState.presetSort = null;
    playerState.query = "";
    document.querySelector("#player-search").value = "";
  }
  if (!isInitial && currentView?.kind === "leaderboard") {
    boardScrollY = window.scrollY;
  }
  leaveFaceOff();
  document.body.classList.add("viewing-player");
  document.querySelector("#player-view").hidden = false;
  document.querySelector("#player-faceoff").href = faceoffHash(participants.players[playerIndex].id);
  renderPlayerView(playerIndex);
  renderPlayerCharts(playerIndex);
  document.title = `${participants.players[playerIndex].name} · ${BASE_TITLE}`;
  if (!isInitial && (!wasPlayer || currentView.index !== playerIndex)) {
    focusHeading("#player-name");
  }
  currentView = { kind: "player", index: playerIndex };
  renderRacerExtras(playerIndex);
  renderRacerResults(playerIndex);
  renderRacerRating(playerIndex);
}

// The pair's corners are player indexes, or null for an empty corner.
function showFaceOff(playerIndexA, playerIndexB, isInitial) {
  const wasFaceOff = currentView && currentView.kind === "faceoff";
  if (!isInitial && currentView?.kind === "leaderboard") {
    boardScrollY = window.scrollY;
  }
  document.body.classList.remove("viewing-player");
  document.querySelector("#player-view").hidden = true;
  document.body.classList.add("viewing-faceoff");
  document.querySelector("#faceoff-view").hidden = false;
  document.querySelector(".section-nav__faceoff").setAttribute("aria-current", "page");
  const names = [playerIndexA, playerIndexB].filter((index) => index !== null).map((index) => participants.players[index].name);
  document.title = names.length === 2 ? `${names[0]} vs ${names[1]} · Face Off · ${BASE_TITLE}` : `Face Off · ${BASE_TITLE}`;
  if (typeof FaceOff !== "undefined") {
    runFeature("FaceOff.show", () => FaceOff.show(playerIndexA, playerIndexB));
  }
  // A new pair picked on the page keeps focus in its picker and the page where it is.
  if (!isInitial && !wasFaceOff) {
    focusHeading("#faceoff-title");
  }
  currentView = { kind: "faceoff" };
}

// Every other view clears Face Off, including a comment draw still waiting for its data.
function leaveFaceOff() {
  document.body.classList.remove("viewing-faceoff");
  document.querySelector("#faceoff-view").hidden = true;
  document.querySelector(".section-nav__faceoff").removeAttribute("aria-current");
  if (typeof FaceOff !== "undefined") {
    runFeature("FaceOff.hide", () => FaceOff.hide());
  }
}

function showLeaderboard(isInitial) {
  const wasPlayer = currentView && currentView.kind === "player";
  const wasFaceOff = currentView && currentView.kind === "faceoff";
  document.body.classList.remove("viewing-player");
  document.querySelector("#player-view").hidden = true;
  leaveFaceOff();
  document.title = BASE_TITLE;
  if (!isInitial && wasPlayer) {
    returnToBoard();
  } else if (!isInitial && wasFaceOff) {
    scrollToSection();
  }
  currentView = { kind: "leaderboard" };
}

// From Face Off, the nav's section links land on their section. The browser jumped to the anchor while the
// board was hidden, so the jump is redone once the board is laid out. A section still waiting for its data
// is hidden, and the board's last place stands in for it.
function scrollToSection() {
  const id = window.location.hash.slice(1);
  const target = id ? document.getElementById(id) : null;
  if (target && !target.closest("[hidden]")) {
    target.scrollIntoView();
  } else {
    returnToBoard();
  }
}

// Back from a racer goes to where the board was left, or to the top of the board's panel when the
// visit began on a racer page. Focus moves to the board's heading either way, without scrolling.
function returnToBoard() {
  if (boardScrollY === null) {
    document.querySelector("#leaderboard").scrollIntoView();
  } else {
    window.scrollTo(0, boardScrollY);
  }
  document.querySelector("#leaderboard-title").focus({ preventScroll: true });
}

function route(isInitial) {
  if (!participants) {
    return;
  }
  const target = parseRoute();
  if (target && target.kind === "faceoff") {
    // An unknown racer leaves their corner empty, and the same racer twice keeps only the red corner.
    const [indexA, indexB] = target.ids.map((id) => (id === null ? null : playerIndexById.get(id) ?? null));
    showFaceOff(indexA, indexB === indexA ? null : indexB, isInitial);
    return;
  }
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
    ratingRankCache = new Map();
    ratingRankDeltaCache = new Map();
    seasonStandingsCache = new Map();
    seasonModeRatingDeltaCache = null;
    leaderboardState.season = currentSeason();

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
    bindSeasonControl();

    renderLeaderboard();
    status.hidden = true;
    document.querySelector("#leaderboard-controls").hidden = false;
    route(true);
    window.addEventListener("hashchange", () => route(false));
    if (typeof TopRated !== "undefined") {
      runFeature("TopRated.init", () => TopRated.init());
    }
    if (typeof Upcoming !== "undefined") {
      runFeature("Upcoming.init", () => Upcoming.init());
    }
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
