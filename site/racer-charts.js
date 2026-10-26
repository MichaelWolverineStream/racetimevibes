// Finish-time scatter, personal trend, and start-time heatmap for the leaderboard and racer pages.
// Loaded between app.js and leaderboard.js. Nothing here touches the DOM at load time, so the
// pure helpers can be exercised outside a browser.

const DAY_MS = 86400000;
const MIN_TREND_FINISHES = 3;
// Two-sided 95% Student t critical values, keyed by degrees of freedom.
const T_CRITICAL_95 = [
  [1, 12.706], [2, 4.303], [3, 3.182], [4, 2.776], [5, 2.571], [6, 2.447],
  [7, 2.365], [8, 2.306], [9, 2.262], [10, 2.228], [11, 2.201], [12, 2.179],
  [13, 2.160], [14, 2.145], [15, 2.131], [16, 2.120], [17, 2.110], [18, 2.101],
  [19, 2.093], [20, 2.086], [21, 2.080], [22, 2.074], [23, 2.069], [24, 2.064],
  [25, 2.060], [26, 2.056], [27, 2.052], [28, 2.048], [29, 2.045], [30, 2.042],
  [40, 2.021], [60, 2.000], [120, 1.980],
];

function dayNumber(isoDate) {
  return Date.parse(`${isoDate}T00:00:00Z`) / DAY_MS;
}

// Between tabulated rows, use the next lower degrees of freedom: its larger value keeps the band conservative.
function tCritical95(df) {
  if (!Number.isInteger(df) || df < 1) {
    throw new RangeError(`Degrees of freedom must be a positive integer, got ${df}.`);
  }
  let value = T_CRITICAL_95[0][1];
  for (const [tabulated, critical] of T_CRITICAL_95) {
    if (tabulated > df) {
      break;
    }
    value = critical;
  }
  return value;
}

// Least-squares fit of finish seconds on day number, with a 95% confidence band for the fitted mean.
// Returns null below three finishes or when every finish falls on the same day.
function linearTrend(points) {
  const n = points.length;
  if (n < MIN_TREND_FINISHES) {
    return null;
  }
  const meanX = points.reduce((sum, point) => sum + point.day, 0) / n;
  const meanY = points.reduce((sum, point) => sum + point.seconds, 0) / n;
  let sxx = 0;
  let sxy = 0;
  points.forEach((point) => {
    const dx = point.day - meanX;
    sxx += dx * dx;
    sxy += dx * (point.seconds - meanY);
  });
  if (sxx === 0) {
    return null;
  }
  const slope = sxy / sxx;
  const intercept = meanY - slope * meanX;
  const sse = points.reduce((sum, point) => sum + (point.seconds - (intercept + slope * point.day)) ** 2, 0);
  const s = Math.sqrt(sse / (n - 2));
  const t = tCritical95(n - 2);
  const predict = (day) => intercept + slope * day;
  return {
    slope,
    intercept,
    n,
    meanX,
    sxx,
    s,
    t,
    predict,
    band: (day) => {
      const margin = t * s * Math.sqrt(1 / n + (day - meanX) ** 2 / sxx);
      return { low: predict(day) - margin, high: predict(day) + margin };
    },
  };
}

// Upper podium count of each colour bin: 0, 1–2, 3–9, 10–24, 25+.
const PODIUM_BIN_MAXIMA = [0, 2, 9, 24, Infinity];
const START_BLOCK_HOURS = 6;
const START_BLOCKS = [0, 6, 12, 18];
const LIMITED_CELL_RACES = 3;
const ALL_RACES = { start: null, end: null };
const FINISH_CHART_HEIGHT = 360;
const FINISH_CHART_MARGIN = { top: 20, right: 18, bottom: 46, left: 62 };
const VIOLIN_CHART_HEIGHT = 500;
const VIOLIN_CHART_MARGIN = { top: 20, right: 18, bottom: 64, left: 62 };
// Drawing every finish takes longer than a keystroke, so typed searches redraw the violins once typing pauses.
const SEARCH_REDRAW_DELAY_MS = 120;
const TIME_TICK_STEPS_MINUTES = [5, 10, 15, 30, 60, 120];
const MAX_TIME_INTERVALS = 6;
const MONTHLY_TICK_MIN_DAYS = 45;
const BAND_SAMPLES = 40;
// Each mode keeps one hue whatever the filters show; a mode missing here is drawn in gray.
const MODE_COLOR_SLOTS = { entrance: 1, fun: 2, glitched: 3, inverted: 4, keys: 5, mystery: 6, simple: 7, spoiler: 8 };
const DENSITY_SAMPLES = 64;

const monthFormatter = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });
const monthYearFormatter = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
const monthDayFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

function podiumBin(count) {
  return PODIUM_BIN_MAXIMA.findIndex((maximum) => count <= maximum);
}

function startBlockLabel(start) {
  return `${compactHour(start)}–${compactHour((start + START_BLOCK_HOURS) % 24)}`;
}

function modeColor(modeName) {
  const slot = MODE_COLOR_SLOTS[modeName];
  return slot ? `var(--mode-${slot})` : "var(--series-muted)";
}

function mostRacedMode(raceIndexes, range) {
  const counts = new Map();
  raceIndexes.forEach((raceIndex) => {
    const race = participants.races[raceIndex];
    if (inRange(race, range)) {
      counts.set(race.m, (counts.get(race.m) ?? 0) + 1);
    }
  });
  let best = null;
  counts.forEach((count, mode) => {
    if (best === null || count > counts.get(best) || (count === counts.get(best) && mode < best)) {
      best = mode;
    }
  });
  return best;
}

function timedFinishes(range, includePlayer) {
  const finishes = [];
  participants.races.forEach((race, raceIndex) => {
    if (!inRange(race, range)) {
      return;
    }
    const day = dayNumber(race.d);
    race.e.forEach((entry) => {
      if (!isFinisher(entry) || entry[3] === null || !includePlayer(entry[0])) {
        return;
      }
      finishes.push({ p: entry[0], m: race.m, day, seconds: entry[3], place: entry[1], n: race.n, raceIndex });
    });
  });
  return finishes;
}

function finishPoints(modeIndex, range, includePlayer) {
  const podiums = new Map(
    computeBoardRows(modeIndex, range).map((row) => [row.p, row.first + row.second + row.third]),
  );
  return timedFinishes(range, includePlayer)
    .filter((finish) => finish.m === modeIndex)
    .map((finish) => ({ ...finish, bin: podiumBin(podiums.get(finish.p) ?? 0) }));
}

function startBlockGrid(playerIndex) {
  const grid = participants.modes.map(() =>
    START_BLOCKS.map(() => ({ races: 0, podiums: 0, wins: 0, losses: 0, draws: 0 })),
  );
  raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
    const race = participants.races[raceIndex];
    const cell = grid[race.m][Math.floor(race.h / START_BLOCK_HOURS)];
    const subject = race.e.find((entry) => entry[0] === playerIndex);
    cell.races += 1;
    if (isFinisher(subject) && subject[1] <= 3) {
      cell.podiums += 1;
    }
    race.e.forEach((entry) => {
      if (entry === subject) {
        return;
      }
      const outcome = compareEntries(subject, entry);
      if (outcome > 0) {
        cell.wins += 1;
      } else if (outcome < 0) {
        cell.losses += 1;
      } else {
        cell.draws += 1;
      }
    });
  });
  return grid;
}

function ordinal(place) {
  const lastTwo = place % 100;
  if (lastTwo >= 11 && lastTwo <= 13) {
    return `${place}th`;
  }
  const suffix = { 1: "st", 2: "nd", 3: "rd" }[place % 10] ?? "th";
  return `${place}${suffix}`;
}

function formatClock(seconds) {
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// Medians of the lower and upper halves, matching _distribution() in scripts/build_stats.py.
function quartiles(values) {
  const sorted = [...values].sort((left, right) => left - right);
  const lower = sorted.slice(0, Math.floor(sorted.length / 2));
  const upper = sorted.slice(Math.ceil(sorted.length / 2));
  const middle = median(sorted);
  return {
    q1: lower.length ? median(lower) : middle,
    median: middle,
    q3: upper.length ? median(upper) : middle,
  };
}

// Gaussian kernel density with Silverman's bandwidth, sampled across the observed range only.
// Returns null when there is no spread to estimate a shape from.
function densityProfile(values) {
  const n = values.length;
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (n < 2 || min === max) {
    return null;
  }
  const mean = values.reduce((sum, value) => sum + value, 0) / n;
  const sd = Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (n - 1));
  const { q1, q3 } = quartiles(values);
  const spread = q3 > q1 ? Math.min(sd, (q3 - q1) / 1.34) : sd;
  const bandwidth = 0.9 * spread * n ** -0.2;
  const step = (max - min) / (DENSITY_SAMPLES - 1);
  const norm = n * bandwidth * Math.sqrt(2 * Math.PI);
  const ys = Array.from({ length: DENSITY_SAMPLES }, (_, index) => min + index * step);
  const densities = ys.map(
    (y) => values.reduce((sum, value) => sum + Math.exp(-0.5 * ((y - value) / bandwidth) ** 2), 0) / norm,
  );
  return { min, max, step, bandwidth, ys, densities, peak: Math.max(...densities) };
}

// Relative violin width (0 to 1) at a finish time, interpolated between the sampled densities.
function profileWidthAt(profile, seconds) {
  const position = (seconds - profile.min) / profile.step;
  const lower = Math.min(DENSITY_SAMPLES - 2, Math.max(0, Math.floor(position)));
  const fraction = Math.min(1, Math.max(0, position - lower));
  const density = profile.densities[lower] + (profile.densities[lower + 1] - profile.densities[lower]) * fraction;
  return density / profile.peak;
}

// A fixed pseudo-random offset in [-1, 1] per finish, so dots keep their place across redraws and filters.
function stableJitter(raceIndex, playerIndex) {
  let hash = Math.imul(raceIndex + 1, 0x9e3779b1) ^ Math.imul(playerIndex + 1, 0x85ebca77);
  hash = Math.imul(hash ^ (hash >>> 16), 0x7feb352d);
  hash = Math.imul(hash ^ (hash >>> 15), 0x846ca68b);
  hash ^= hash >>> 16;
  return ((hash >>> 0) / 0xffffffff) * 2 - 1;
}

function timeTicks(minSeconds, maxSeconds) {
  const stepsInSeconds = TIME_TICK_STEPS_MINUTES.map((minutes) => minutes * 60);
  const bounds = (step) => [Math.floor(minSeconds / step) * step, Math.ceil(maxSeconds / step) * step];
  const step =
    stepsInSeconds.find((candidate) => {
      const [low, high] = bounds(candidate);
      return (high - low) / candidate <= MAX_TIME_INTERVALS;
    }) ?? stepsInSeconds.at(-1);
  let [min, max] = bounds(step);
  if (min === max) {
    min -= step;
    max += step;
  } else if (minSeconds === maxSeconds) {
    min = Math.min(min, minSeconds - step);
    max = Math.max(max, maxSeconds + step);
  }
  const ticks = [];
  for (let tick = min; tick <= max; tick += step) {
    ticks.push(tick);
  }
  return { min, max, ticks };
}

function dateTicks(startDay, endDay) {
  const ticks = [];
  if (endDay - startDay >= MONTHLY_TICK_MIN_DAYS) {
    const start = new Date(startDay * DAY_MS);
    const crossesYear = start.getUTCFullYear() !== new Date(endDay * DAY_MS).getUTCFullYear();
    let month = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
    while (month.getTime() / DAY_MS <= endDay) {
      const day = month.getTime() / DAY_MS;
      if (day >= startDay) {
        const label = crossesYear && month.getUTCMonth() === 0 ? monthYearFormatter : monthFormatter;
        ticks.push({ day, label: label.format(month) });
      }
      month = new Date(Date.UTC(month.getUTCFullYear(), month.getUTCMonth() + 1, 1));
    }
    return ticks;
  }
  for (let day = startDay; day <= endDay; day += 7) {
    ticks.push({ day, label: monthDayFormatter.format(new Date(day * DAY_MS)) });
  }
  return ticks;
}

function emptyMessage(text) {
  const message = document.createElement("p");
  message.className = "table-empty";
  message.textContent = text;
  return message;
}

function pointLabel(point) {
  const race = participants.races[point.raceIndex];
  return `${participants.players[point.p].name} · ${formatDuration(point.seconds)} · ${ordinal(point.place)} of ${point.n} · ${formatRaceDate(race.d)}`;
}

function bindPointTooltip(svg, points) {
  svg.addEventListener("mousemove", (event) => {
    const dot = event.target.closest("circle[data-i]");
    if (dot) {
      showTooltip(dot, pointLabel(points[dot.dataset.i]), event);
    } else {
      hideTooltip();
    }
  });
  svg.addEventListener("mouseleave", hideTooltip);
}

// Horizontal grid, clock labels, and the rotated title for the shared finish-time axis.
function appendTimeAxis(svg, { time, y, margin, width, height }) {
  time.ticks.forEach((tick, index) => {
    svg.append(
      svgElement("line", {
        class: index === 0 ? "baseline" : "grid-line",
        x1: margin.left,
        x2: width - margin.right,
        y1: y(tick),
        y2: y(tick),
      }),
      svgElement(
        "text",
        { class: "axis-label", x: margin.left - 10, y: y(tick) + 4, "text-anchor": "end" },
        formatClock(tick),
      ),
    );
  });
  const titleY = margin.top + (height - margin.top - margin.bottom) / 2;
  svg.append(
    svgElement(
      "text",
      { class: "axis-label", x: 14, y: titleY, transform: `rotate(-90 14 ${titleY})`, "text-anchor": "middle" },
      "FINISH TIME",
    ),
  );
}

// Shared frame for the finish-time charts: grid, time axis, date axis, and axis titles.
function finishChartFrame({ id, className, title, description, width, points, xDomain }) {
  const height = FINISH_CHART_HEIGHT;
  const margin = FINISH_CHART_MARGIN;
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const seconds = points.map((point) => point.seconds);
  const time = timeTicks(Math.min(...seconds), Math.max(...seconds));
  const [startDay, endDay] = xDomain;
  const span = endDay - startDay || 1;
  const x = (day) => Math.min(width - margin.right, Math.max(margin.left, margin.left + ((day - startDay) / span) * plotWidth));
  const y = (value) => margin.top + plotHeight - ((value - time.min) / (time.max - time.min)) * plotHeight;

  const svg = svgElement("svg", {
    class: className,
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": `${id}-title ${id}-description`,
  });
  svg.append(
    svgElement("title", { id: `${id}-title` }, title),
    svgElement("desc", { id: `${id}-description` }, description),
  );
  const clip = svgElement("clipPath", { id: `${id}-plot` });
  clip.append(svgElement("rect", { x: margin.left, y: margin.top, width: plotWidth, height: plotHeight }));
  const defs = svgElement("defs");
  defs.append(clip);
  svg.append(defs);
  appendTimeAxis(svg, { time, y, margin, width, height });
  dateTicks(Math.ceil(startDay), Math.floor(endDay)).forEach((tick) => {
    svg.append(
      svgElement(
        "text",
        { class: "axis-label", x: x(tick.day), y: margin.top + plotHeight + 18, "text-anchor": "middle" },
        tick.label,
      ),
    );
  });
  svg.append(
    svgElement(
      "text",
      { class: "axis-label", x: margin.left + plotWidth / 2, y: height - 8, "text-anchor": "middle" },
      "RACE DATE",
    ),
  );
  return { svg, x, y, clipPath: `url(#${id}-plot)` };
}

function finishScatterChart(points, { id, highlightPlayer, title, description, xDomain }, width) {
  const { svg, x, y } = finishChartFrame({
    id,
    className: highlightPlayer === null ? "chart-svg" : "chart-svg chart-svg--faded",
    title,
    description,
    width,
    points,
    xDomain,
  });
  const isHighlighted = (point) => point.p === highlightPlayer;
  // Ascending bin keeps top racers on top; the highlighted racer is drawn last of all.
  const order = points
    .map((point, index) => index)
    .sort((left, right) => {
      const a = points[left];
      const b = points[right];
      return isHighlighted(a) - isHighlighted(b) || a.bin - b.bin;
    });
  svg.append(
    ...order.map((index) => {
      const point = points[index];
      const highlighted = isHighlighted(point);
      return svgElement("circle", {
        class: `scatter-dot scatter-dot--bin-${point.bin}${highlighted ? " scatter-dot--highlight" : ""}`,
        cx: x(point.day),
        cy: y(point.seconds),
        r: highlighted ? 3.5 : 2.5,
        "data-i": index,
      });
    }),
  );
  bindPointTooltip(svg, points);
  return svg;
}

function finishTrendChart(points, trend, { id, title, description }, width) {
  const days = points.map((point) => point.day);
  const first = Math.min(...days);
  const last = Math.max(...days);
  const pad = Math.max(1, (last - first) * 0.03);
  const { svg, x, y, clipPath } = finishChartFrame({
    id,
    className: "chart-svg",
    title,
    description,
    width,
    points,
    xDomain: [first - pad, last + pad],
  });
  if (trend) {
    const sampled = Array.from({ length: BAND_SAMPLES }, (_, index) => first + ((last - first) * index) / (BAND_SAMPLES - 1));
    const edge = (day, value) => `${x(day)},${y(value)}`;
    const polygon = [
      ...sampled.map((day) => edge(day, trend.band(day).high)),
      ...sampled.reverse().map((day) => edge(day, trend.band(day).low)),
    ].join(" ");
    // With few finishes the band can be far taller than the data; clip it rather than squash the dots.
    const marks = svgElement("g", { "clip-path": clipPath });
    marks.append(
      svgElement("polygon", { class: "trend-band", points: polygon }),
      svgElement("line", {
        class: "trend-line",
        x1: x(first),
        x2: x(last),
        y1: y(trend.predict(first)),
        y2: y(trend.predict(last)),
      }),
    );
    svg.append(marks);
  }
  svg.append(
    ...points.map((point, index) =>
      svgElement("circle", { class: "trend-dot", cx: x(point.day), cy: y(point.seconds), r: 3, "data-i": index }),
    ),
  );
  bindPointTooltip(svg, points);
  return svg;
}

function violinPointLabel(finish) {
  const race = participants.races[finish.raceIndex];
  const name = participants.players[finish.p].name;
  return `${name} · ${modeLabel(participants.modes[finish.m])} · ${formatDuration(finish.seconds)} · ${ordinal(finish.place)} of ${finish.n} · ${formatRaceDate(race.d)}`;
}

function violinSummary({ center, maxHalf, y, profile, stats, label }) {
  const group = svgElement("g", { class: "chart-mark violin-summary", tabindex: 0, role: "img", "aria-label": label });
  const halfLength = Math.max(6, (profile ? profileWidthAt(profile, stats.median) : 0.3) * maxHalf);
  const level = y(stats.median);
  group.append(
    svgElement("rect", {
      class: "focus-ring",
      x: center - halfLength - 4,
      y: level - 5,
      width: halfLength * 2 + 8,
      height: 10,
      rx: 2,
    }),
    svgElement("line", {
      class: "violin-median",
      x1: center - halfLength,
      x2: center + halfLength,
      y1: level,
      y2: level,
    }),
  );
  group.addEventListener("focus", () => showTooltip(group, label));
  group.addEventListener("blur", hideTooltip);
  return group;
}

// One violin per mode on a shared time axis: density wash, every finish as a dot, and a median mark.
function violinChart(finishes, { id, emphasisMode, highlightPlayer, title, description }, width) {
  const height = VIOLIN_CHART_HEIGHT;
  const margin = VIOLIN_CHART_MARGIN;
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const plotBottom = margin.top + plotHeight;
  const band = plotWidth / participants.modes.length;
  const maxHalf = band * 0.4;
  const seconds = finishes.map((finish) => finish.seconds);
  const time = timeTicks(Math.min(...seconds), Math.max(...seconds));
  const y = (value) => plotBottom - ((value - time.min) / (time.max - time.min)) * plotHeight;

  const svg = svgElement("svg", {
    class: highlightPlayer === null ? "chart-svg" : "chart-svg chart-svg--faded",
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": `${id}-title ${id}-description`,
  });
  svg.append(
    svgElement("title", { id: `${id}-title` }, title),
    svgElement("desc", { id: `${id}-description` }, description),
  );
  appendTimeAxis(svg, { time, y, margin, width, height });

  const indexesByMode = participants.modes.map(() => []);
  finishes.forEach((finish, index) => indexesByMode[finish.m].push(index));

  const groups = [];
  const labels = [];
  participants.modes.forEach((modeName, modeIndex) => {
    const indexes = indexesByMode[modeIndex];
    const values = indexes.map((index) => finishes[index].seconds);
    const center = margin.left + band * (modeIndex + 0.5);
    const color = `--violin-color: ${modeColor(modeName)}`;
    const muted = emphasisMode !== null && modeIndex !== emphasisMode;
    const group = svgElement("g", { class: muted ? "violin violin--muted" : "violin", style: color });
    const profile = values.length ? densityProfile(values) : null;
    if (profile) {
      const edge = (sign) => profile.ys.map((value, step) => `${center + sign * (profile.densities[step] / profile.peak) * maxHalf},${y(value)}`);
      group.append(svgElement("path", { class: "violin-wash", d: `M${edge(1).join("L")}L${edge(-1).reverse().join("L")}Z` }));
    }
    const dots = indexes.map((index) => {
      const finish = finishes[index];
      const halfWidth = profile ? profileWidthAt(profile, finish.seconds) * maxHalf : maxHalf * 0.15;
      const highlighted = finish.p === highlightPlayer;
      return {
        highlighted,
        element: svgElement("circle", {
          class: highlighted ? "violin-dot violin-dot--highlight" : "violin-dot",
          cx: center + halfWidth * stableJitter(finish.raceIndex, finish.p),
          cy: y(finish.seconds),
          r: highlighted ? 3.5 : 2,
          "data-i": index,
        }),
      };
    });
    // The highlighted racer is drawn last so the outlined dots stay on top.
    group.append(...dots.filter((dot) => !dot.highlighted).map((dot) => dot.element));
    group.append(...dots.filter((dot) => dot.highlighted).map((dot) => dot.element));
    if (values.length) {
      const stats = quartiles(values);
      const racers = new Set(indexes.map((index) => finishes[index].p)).size;
      const label = `${modeLabel(modeName)}: ${integerFormatter.format(values.length)} finishes by ${integerFormatter.format(racers)} racers; median ${formatDuration(stats.median)}; middle half ${formatDuration(stats.q1)}–${formatDuration(stats.q3)}`;
      group.append(violinSummary({ center, maxHalf, y, profile, stats, label }));
    }
    groups.push(group);
    labels.push(
      svgElement("circle", { class: "violin-swatch", cx: center, cy: plotBottom + 12, r: 5, style: color }),
      svgElement(
        "text",
        { class: "axis-label axis-label--mode", x: center, y: plotBottom + 32, "text-anchor": "middle" },
        modeLabel(modeName).toUpperCase(),
      ),
      svgElement(
        "text",
        { class: "axis-label axis-label--sample", x: center, y: plotBottom + 46, "text-anchor": "middle" },
        `${integerFormatter.format(values.length)} FINISHES`,
      ),
    );
  });
  svg.append(...groups, ...labels);

  // One delegated handler: the main page draws thousands of dots.
  svg.addEventListener("mousemove", (event) => {
    const dot = event.target.closest("circle[data-i]");
    if (dot) {
      showTooltip(dot, violinPointLabel(finishes[dot.dataset.i]), event);
      return;
    }
    const summary = event.target.closest(".violin-summary");
    if (summary) {
      showTooltip(summary, summary.getAttribute("aria-label"), event);
    } else {
      hideTooltip();
    }
  });
  svg.addEventListener("mouseleave", hideTooltip);
  return svg;
}

function renderStartHeatmap(container, grid, metric) {
  const wrapper = document.createElement("div");
  wrapper.className = "start-heatmap";
  wrapper.setAttribute("role", "group");
  wrapper.setAttribute("aria-label", "Races by mode and Eastern start time");

  const corner = document.createElement("div");
  corner.className = "heatmap-corner";
  corner.textContent = "Eastern";
  wrapper.append(corner);
  START_BLOCKS.forEach((start) => {
    const header = document.createElement("div");
    header.className = "heatmap-hour";
    header.textContent = startBlockLabel(start);
    wrapper.append(header);
  });

  participants.modes.forEach((mode, modeIndex) => {
    const rowLabel = document.createElement("div");
    rowLabel.className = "heatmap-row-label";
    rowLabel.textContent = modeLabel(mode).toUpperCase();
    wrapper.append(rowLabel);

    START_BLOCKS.forEach((start, blockIndex) => {
      const { races, podiums, wins, losses, draws } = grid[modeIndex][blockIndex];
      const h2h = winRate(wins, losses);
      const podiumRate = races ? podiums / races : null;
      const isLimited = races > 0 && races < LIMITED_CELL_RACES;
      let token = null;
      let alpha = 0;
      if (races > 0 && metric === "h2h" && h2h !== null && h2h !== 0.5) {
        token = h2h >= 0.5 ? "--positive" : "--negative";
        alpha = 0.12 + (Math.abs(h2h - 0.5) / 0.5) * 0.63;
      } else if (races > 0 && metric === "podium") {
        token = "--gold";
        alpha = podiumRate * 0.75;
      }
      if (isLimited) {
        alpha *= 0.48;
      }

      const cell = document.createElement("div");
      cell.className = "heatmap-cell";
      if (isLimited) {
        cell.classList.add("heatmap-cell--limited");
      }
      cell.tabIndex = 0;
      cell.setAttribute("role", "img");
      cell.style.backgroundColor = token
        ? `color-mix(in srgb, var(${token}) ${Math.round(alpha * 100)}%, transparent)`
        : "transparent";
      cell.textContent = races === 0 ? "·" : integerFormatter.format(races);
      const where = `${modeLabel(mode)}, ${fullHour(start)}–${fullHour((start + START_BLOCK_HOURS) % 24)}`;
      const label = races === 0
        ? `${where}: no races`
        : `${where}: ${integerFormatter.format(races)} races; H2H ${formatRate(h2h)} (${integerFormatter.format(wins)} W, ${integerFormatter.format(losses)} L, ${integerFormatter.format(draws)} D); podiums ${integerFormatter.format(podiums)} (${formatRate(podiumRate)})`;
      cell.setAttribute("aria-label", label);
      bindTooltip(cell, label);
      wrapper.append(cell);
    });
  });
  container.replaceChildren(wrapper);
}

function setNote(selector, text) {
  const note = document.querySelector(selector);
  note.hidden = text === null;
  note.textContent = text ?? "";
}

function modeSummaryRows(finishes) {
  return participants.modes.map((modeName, modeIndex) => {
    const inMode = finishes.filter((finish) => finish.m === modeIndex);
    if (!inMode.length) {
      return [modeLabel(modeName), "0", "0", "--", "--", "--", "--"];
    }
    const seconds = inMode.map((finish) => finish.seconds);
    const stats = quartiles(seconds);
    return [
      modeLabel(modeName),
      integerFormatter.format(new Set(inMode.map((finish) => finish.p)).size),
      integerFormatter.format(inMode.length),
      formatDuration(stats.q1),
      formatDuration(stats.median),
      formatDuration(stats.q3),
      formatDuration(Math.min(...seconds)),
    ];
  });
}

let pendingFinishTimes = null;
const drawnChartKeys = new Map();

// True when the host already shows this key, so a redraw that would change nothing (a table sort) can be skipped.
function isChartCurrent(host, key) {
  if (drawnChartKeys.get(host) === key) {
    return true;
  }
  drawnChartKeys.set(host, key);
  return false;
}

function renderLeaderboardFinishTimesSoon(visibleRows, modeId, range) {
  clearTimeout(pendingFinishTimes);
  pendingFinishTimes = setTimeout(() => renderLeaderboardFinishTimes(visibleRows, modeId, range), SEARCH_REDRAW_DELAY_MS);
}

function renderLeaderboardFinishTimes(visibleRows, modeId, range) {
  clearTimeout(pendingFinishTimes);
  const host = document.querySelector("#finish-violin-chart");
  const table = document.querySelector("#finish-violin-table");
  const showEmpty = (text) => {
    mountChart(host, () => emptyMessage(text));
    table.replaceChildren();
  };
  const racers = new Set(visibleRows.map((row) => row.p));
  const key = [modeId, range.start, range.end, [...racers].sort((left, right) => left - right).join(",")].join("|");
  if (isChartCurrent(host, key)) {
    return;
  }
  if (!visibleRows.length) {
    showEmpty("No racers match these filters.");
    return;
  }
  const finishes = timedFinishes(range, (playerIndex) => racers.has(playerIndex));
  if (!finishes.length) {
    showEmpty("No recorded finish times for these racers during the selected dates.");
    return;
  }
  const emphasisMode = modeId === ALL_MODES ? null : modeIndexFor(modeId);
  mountChart(host, (width) =>
    violinChart(
      finishes,
      {
        id: "finish-violin",
        emphasisMode,
        highlightPlayer: null,
        title: "Finish times by mode",
        description: "One violin per mode: each dot is one finish, the shape shows where finishes cluster, and the bold line marks the median.",
      },
      width,
    ),
  );
  renderTable(table, ["Mode", "Racers", "Finishes", "Q1", "Median", "Q3", "Best"], modeSummaryRows(finishes));
}

function renderPlayerFinishTable(own) {
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Date", "Place", "Field", "Time"].forEach((header) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = header;
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  [...own].sort((left, right) => right.raceIndex - left.raceIndex).forEach((point) => {
    const race = participants.races[point.raceIndex];
    const link = document.createElement("a");
    link.href = `${RACE_ROOT}${race.r}`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = formatRaceDate(race.d);
    const row = document.createElement("tr");
    const dateCell = document.createElement("td");
    dateCell.append(link);
    row.append(dateCell);
    [ordinal(point.place), integerFormatter.format(point.n), formatDuration(point.seconds)].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    body.append(row);
  });
  table.append(head, body);
  document.querySelector("#player-finish-table").replaceChildren(table);
}

function renderPlayerCharts(playerIndex) {
  const modeId = playerState.mode;
  const name = participants.players[playerIndex].name;
  const mode = modeId === ALL_MODES
    ? mostRacedMode(raceIndexesByPlayer[playerIndex], ALL_RACES)
    : modeIndexFor(modeId);
  const modeName = modeLabel(participants.modes[mode]);
  setNote(
    "#player-scatter-note",
    modeId === ALL_MODES
      ? `Finish times aren't comparable across modes, so these charts show ${modeName}, ${name}'s most-raced mode. Pick a mode above to change it.`
      : null,
  );
  const field = finishPoints(mode, ALL_RACES, () => true);
  const own = field.filter((point) => point.p === playerIndex);
  const trend = linearTrend(own);
  document.querySelector("#player-legend-name").textContent = name;
  document.querySelector("#player-legend-key").className = `podium-key podium-key--${own.length ? own[0].bin : 0} podium-key--highlight`;
  const xDomain = [dayNumber(participants.races[0].d), dayNumber(participants.races.at(-1).d)];

  mountChart(document.querySelector("#player-scatter-chart"), (width) =>
    field.length
      ? finishScatterChart(
          field,
          {
            id: "player-scatter",
            highlightPlayer: playerIndex,
            title: `${name} against the field in ${modeName}`,
            description: `Finish times for every racer in ${modeName}, with ${name}'s finishes highlighted.`,
            xDomain,
          },
          width,
        )
      : emptyMessage("No recorded finish times in this mode."),
  );
  mountChart(document.querySelector("#player-trend-chart"), (width) =>
    own.length
      ? finishTrendChart(
          own,
          trend,
          {
            id: "player-trend",
            title: `${name}'s finish times in ${modeName}`,
            description: trend
              ? "Finish times with a linear trend line and its 95% confidence band."
              : "Finish times by race date.",
          },
          width,
        )
      : emptyMessage("No recorded finish times in this mode."),
  );
  // The every-mode violin ignores the mode toggle, so it only redraws for a different racer.
  const violinHost = document.querySelector("#player-violin-chart");
  if (!isChartCurrent(violinHost, playerIndex)) {
    const everyone = timedFinishes(ALL_RACES, () => true);
    mountChart(violinHost, (width) =>
      violinChart(
        everyone,
        {
          id: "player-violin",
          emphasisMode: null,
          highlightPlayer: playerIndex,
          title: `${name} in every mode`,
          description: `Every finish in every mode, with ${name}'s finishes highlighted.`,
        },
        width,
      ),
    );
  }
  renderPlayerFinishTable(own);
  renderPlayerHeatmap(playerIndex);
}

function renderPlayerHeatmap(playerIndex) {
  const metric = playerState.heatmapMetric;
  document.querySelectorAll("#player-heatmap-metric [data-metric]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.metric === metric));
  });
  document.querySelectorAll("#player-view [data-legend]").forEach((legend) => {
    legend.hidden = legend.dataset.legend !== metric;
  });
  renderStartHeatmap(document.querySelector("#player-start-heatmap"), startBlockGrid(playerIndex), metric);
}
