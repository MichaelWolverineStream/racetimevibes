// The preset violins and presets table shared by the racer pages and the records, plus the
// step-chart pieces shared by the record progression and the racer's rating.
// Loaded between app.js and leaderboard.js. Nothing here touches the DOM at load time, so the
// pure helpers can be exercised outside a browser.

const DAY_MS = 86400000;

function dayNumber(isoDate) {
  return Date.parse(`${isoDate}T00:00:00Z`) / DAY_MS;
}

const ALL_RACES = { start: null, end: null };
const VIOLIN_CHART_HEIGHT = 500;
// The bottom fits the tallest label block: swatch, two name lines, a mode line and the finish count,
// so the plot keeps its height whichever labels a chart needs.
const VIOLIN_CHART_MARGIN = { top: 20, right: 18, bottom: 92, left: 62 };
const VIOLIN_MARK_MIN_HALF = 6;
// A preset needs this many races, by anyone, before its finishes are drawn as a shape.
const VIOLIN_MIN_PRESET_RACES = 10;
// Every preset chart has this many equal slots, so a violin is the same width in every chart.
const VIOLIN_SLOTS = 8;
// About 100px per slot plus margins; a narrower host scrolls sideways rather than squeezing the labels.
const VIOLIN_MIN_WIDTH = 880;
// How far from a dot, in CSS pixels, a tap still reads it: about half a fingertip.
const VIOLIN_TAP_REACH = 16;
// Preset labels are measured on a canvas to wrap them. Must match .axis-label--mode in styles.css.
const VIOLIN_LABEL_FONT_SIZE = "700 13px";
const VIOLIN_LABEL_LINE_HEIGHT = 14;
const VIOLIN_LABEL_GAP = 10;
const TIME_TICK_STEPS_MINUTES = [5, 10, 15, 30, 60, 120];
const MAX_TIME_INTERVALS = 6;
const MONTHLY_TICK_MIN_DAYS = 45;
// Each mode keeps one hue whatever the filters show; a mode missing here is drawn in gray.
const MODE_COLOR_SLOTS = { entrance: 1, fun: 2, glitched: 3, inverted: 4, keys: 5, mystery: 6, simple: 7, spoiler: 8 };
const DENSITY_SAMPLES = 64;
// Step-chart markers. styles.css grows each dot by 2 on hover; change both together.
const STEP_DOT_RADIUS = 4;
const STEP_CURRENT_RADIUS = 5;
const STEP_HALO_RADIUS = 10;
const STEP_HIT_RADIUS = 12;
// The racer page's presets table opens like the one in Hall of records.
const PLAYER_PRESET_SORT = { key: "races", direction: "desc" };

const monthFormatter = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });
const monthYearFormatter = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
const monthDayFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

function modeColor(modeName) {
  const slot = MODE_COLOR_SLOTS[modeName];
  return slot ? `var(--mode-${slot})` : "var(--series-muted)";
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

function timeTicks(minSeconds, maxSeconds, maxIntervals = MAX_TIME_INTERVALS) {
  const stepsInSeconds = TIME_TICK_STEPS_MINUTES.map((minutes) => minutes * 60);
  const bounds = (step) => [Math.floor(minSeconds / step) * step, Math.ceil(maxSeconds / step) * step];
  const step =
    stepsInSeconds.find((candidate) => {
      const [low, high] = bounds(candidate);
      return (high - low) / candidate <= maxIntervals;
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

// Given the plot width, month labels thin out by MONTH_LABEL_STEPS so neighbours never overlap.
function dateTicks(startDay, endDay, plotWidth = Infinity) {
  const ticks = [];
  if (endDay - startDay >= MONTHLY_TICK_MIN_DAYS) {
    const start = new Date(startDay * DAY_MS);
    const crossesYear = start.getUTCFullYear() !== new Date(endDay * DAY_MS).getUTCFullYear();
    const monthWidth = (plotWidth / (endDay - startDay)) * (365.25 / 12);
    const labelStep = MONTH_LABEL_STEPS.find((candidate) => candidate * monthWidth >= MONTH_LABEL_MIN_SPACING) ?? 12;
    let month = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
    while (month.getTime() / DAY_MS <= endDay) {
      const day = month.getTime() / DAY_MS;
      if (day >= startDay && month.getUTCMonth() % labelStep === 0) {
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

// Horizontal grid, clock labels, and the optional rotated title for the shared finish-time axis.
function appendTimeAxis(svg, { time, y, margin, width, height, title = true }) {
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
  if (!title) return;
  const titleY = margin.top + (height - margin.top - margin.bottom) / 2;
  svg.append(
    svgElement(
      "text",
      { class: "axis-label", x: 14, y: titleY, transform: `rotate(-90 14 ${titleY})`, "text-anchor": "middle" },
      "FINISH TIME",
    ),
  );
}

// Shared frame for the finish-time step charts: grid, time axis, date axis, and axis titles.
// layout is { height, margin, axisTitles, maxTimeIntervals }.
function finishChartFrame({ id, className, title, description, width, points, xDomain, layout }) {
  const { height, margin, axisTitles, maxTimeIntervals } = layout;
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const seconds = points.map((point) => point.seconds);
  const time = timeTicks(Math.min(...seconds), Math.max(...seconds), maxTimeIntervals);
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
  appendTimeAxis(svg, { time, y, margin, width, height, title: axisTitles });
  dateTicks(Math.ceil(startDay), Math.floor(endDay), plotWidth).forEach((tick) => {
    svg.append(
      svgElement(
        "text",
        { class: "axis-label", x: x(tick.day), y: margin.top + plotHeight + 18, "text-anchor": "middle" },
        tick.label,
      ),
    );
  });
  if (axisTitles) {
    svg.append(
      svgElement(
        "text",
        { class: "axis-label", x: margin.left + plotWidth / 2, y: height - 8, "text-anchor": "middle" },
        "RACE DATE",
      ),
    );
  }
  return { svg, defs, x, y, plotBottom: margin.top + plotHeight, clipPath: `url(#${id}-plot)` };
}

// A staircase through screen points where each value holds until the next one, and the last
// holds to endX. Point i lands on corner 2i: one corner ends the old run, the next moves to the
// new value. `at` is each point's share of the line's length, so on the draw-in its marker can
// appear as the line reaches it. The wash fades out toward the plot floor, which is not zero,
// so it implies no magnitude.
function stepLine(defs, { id, points, endX, plotBottom, clipPath }) {
  const corners = [];
  points.forEach(([px, py], index) => {
    if (index > 0) {
      corners.push([px, points[index - 1][1]]);
    }
    corners.push([px, py]);
  });
  corners.push([endX, points.at(-1)[1]]);
  const travelled = [0];
  corners.slice(1).forEach(([cx, cy], index) => {
    const [px, py] = corners[index];
    travelled.push(travelled[index] + Math.abs(cx - px) + Math.abs(cy - py));
  });
  const length = travelled.at(-1) || 1;
  const path = corners.map(([cx, cy], index) => `${index ? "L" : "M"}${cx},${cy}`).join("");

  const gradient = svgElement("linearGradient", { id: `${id}-wash`, x1: 0, y1: 0, x2: 0, y2: 1 });
  gradient.append(
    svgElement("stop", { class: "step-chart__wash-top", offset: 0 }),
    svgElement("stop", { class: "step-chart__wash-bottom", offset: 1 }),
  );
  defs.append(gradient);
  return {
    wash: svgElement("path", {
      class: "step-chart__wash",
      d: `${path}V${plotBottom}H${points[0][0]}Z`,
      fill: `url(#${id}-wash)`,
      "clip-path": clipPath,
    }),
    line: svgElement("path", { class: "step-chart__line", d: path, pathLength: 1 }),
    at: points.map((point, index) => travelled[index * 2] / length),
  };
}

// A focusable marker on a step chart. kind is "first" (hollow), "break" or "current" (with a halo).
function stepMark({ cx, cy, kind, label, at }) {
  const isCurrent = kind === "current";
  const mark = svgElement("g", { class: "chart-mark", tabindex: 0, role: "img", "aria-label": label });
  mark.style.setProperty("--at", at.toFixed(3));
  if (isCurrent) {
    mark.appendChild(svgElement("circle", { class: "step-chart__halo", cx, cy, r: STEP_HALO_RADIUS }));
  }
  mark.append(
    svgElement("circle", { class: "step-chart__hit", cx, cy, r: STEP_HIT_RADIUS }),
    svgElement("circle", {
      class: `step-chart__dot step-chart__dot--${kind}`,
      cx,
      cy,
      r: isCurrent ? STEP_CURRENT_RADIUS : STEP_DOT_RADIUS,
    }),
  );
  bindTooltip(mark, label);
  return mark;
}

function violinPointLabel(finish, groupLabel) {
  const race = participants.races[finish.raceIndex];
  const name = participants.players[finish.p].name;
  return `${name} · ${groupLabel} · ${formatDuration(finish.seconds)} · ${ordinal(finish.place)} of ${finish.n} · ${formatRaceDate(race.d)}`;
}

let axisLabelContext = null;

function axisLabelFont() {
  return `${VIOLIN_LABEL_FONT_SIZE} ${getComputedStyle(document.documentElement).getPropertyValue("--body").trim()}`;
}

function ellipsizeLabel(context, text, maxWidth) {
  if (context.measureText(text).width <= maxWidth) {
    return text;
  }
  const characters = [...text];
  while (characters.length && context.measureText(`${characters.join("").trimEnd()}…`).width > maxWidth) {
    characters.pop();
  }
  return `${characters.join("").trimEnd()}…`;
}

// Splits a label into at most two lines that each fit maxWidth; whatever still doesn't fit ends in an ellipsis.
function wrapAxisLabel(text, maxWidth) {
  axisLabelContext ??= document.createElement("canvas").getContext("2d");
  axisLabelContext.font = axisLabelFont();
  const words = text.split(" ");
  let first = words.shift();
  while (words.length && axisLabelContext.measureText(`${first} ${words[0]}`).width <= maxWidth) {
    first += ` ${words.shift()}`;
  }
  const lines = [first, ...(words.length ? [words.join(" ")] : [])];
  return lines.map((line) => ellipsizeLabel(axisLabelContext, line, maxWidth));
}

// Half-length of a line drawn across a violin at a finish time. Outside the violin's range,
// or with no shape to follow, it falls back to a short stub so the line stays visible.
function violinHalfLength(profile, seconds, maxHalf) {
  if (profile === null) {
    return Math.max(VIOLIN_MARK_MIN_HALF, 0.3 * maxHalf);
  }
  const inside = seconds >= profile.min && seconds <= profile.max;
  return Math.max(VIOLIN_MARK_MIN_HALF, (inside ? profileWidthAt(profile, seconds) : 0) * maxHalf);
}

// A focusable horizontal line across a violin, with its own tooltip.
function violinMark({ center, halfLength, level, className, label }) {
  const group = svgElement("g", { class: "chart-mark violin-summary", tabindex: 0, role: "img", "aria-label": label });
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
      class: className,
      x1: center - halfLength,
      x2: center + halfLength,
      y1: level,
      y2: level,
    }),
  );
  // A tap's replayed focus must not cover the dot the tap read (see TAP_ECHO_MS in app.js).
  group.addEventListener("focus", () => {
    if (!tapEchoing()) {
      showTooltip(group, label);
    }
  });
  group.addEventListener("blur", () => {
    if (!tapEchoing()) {
      hideTooltip();
    }
  });
  return group;
}

// One violin per group on a shared time axis: density wash, every finish as a dot, a median mark,
// and the median winning time as a gold line. The groups sit centred in VIOLIN_SLOTS equal slots.
// Each group is { label, name, modeLine, color, indexes, median, gold }: indexes point into finishes,
// gold is { median, wins } or null, and modeLine is null unless the chart mixes modes.
function violinChart(finishes, groups, { id, highlightPlayer, title, description }, width) {
  const height = VIOLIN_CHART_HEIGHT;
  const margin = VIOLIN_CHART_MARGIN;
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const plotBottom = margin.top + plotHeight;
  const band = plotWidth / VIOLIN_SLOTS;
  const maxHalf = band * 0.4;
  const firstSlot = (VIOLIN_SLOTS - groups.length) / 2;
  const seconds = finishes.map((finish) => finish.seconds);
  const time = timeTicks(Math.min(...seconds), Math.max(...seconds));
  const y = (value) => plotBottom - ((value - time.min) / (time.max - time.min)) * plotHeight;
  // Every label block in a chart shares its rows, so the finish counts line up.
  const nameLines = groups.map((group) => wrapAxisLabel(group.name, band - VIOLIN_LABEL_GAP));
  const nameRows = Math.max(...nameLines.map((lines) => lines.length));
  const hasModeLine = groups.some((group) => group.modeLine !== null);
  const labelRow = (row) => plotBottom + 32 + row * VIOLIN_LABEL_LINE_HEIGHT;

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

  const violins = [];
  const labels = [];
  groups.forEach((group, groupIndex) => {
    const { indexes, median: medianSeconds, gold } = group;
    const values = indexes.map((index) => finishes[index].seconds);
    const center = margin.left + band * (firstSlot + groupIndex + 0.5);
    const color = `--violin-color: ${group.color}`;
    const violin = svgElement("g", { class: "violin", style: color });
    const profile = values.length ? densityProfile(values) : null;
    if (profile) {
      const edge = (sign) => profile.ys.map((value, step) => `${center + sign * (profile.densities[step] / profile.peak) * maxHalf},${y(value)}`);
      violin.append(svgElement("path", { class: "violin-wash", d: `M${edge(1).join("L")}L${edge(-1).reverse().join("L")}Z` }));
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
          "data-g": groupIndex,
        }),
      };
    });
    // The highlighted racer is drawn last so the outlined dots stay on top.
    violin.append(...dots.filter((dot) => !dot.highlighted).map((dot) => dot.element));
    violin.append(...dots.filter((dot) => dot.highlighted).map((dot) => dot.element));
    // Drawn before the median so the median stays on top when the two are close.
    if (gold) {
      violin.append(
        violinMark({
          center,
          halfLength: violinHalfLength(profile, gold.median, maxHalf),
          level: y(gold.median),
          className: "violin-gold",
          label: `${group.label}: gold median ${formatDuration(gold.median)} from ${integerFormatter.format(gold.wins)} winning times`,
        }),
      );
    }
    if (values.length && medianSeconds !== null) {
      const stats = quartiles(values);
      const racers = new Set(indexes.map((index) => finishes[index].p)).size;
      violin.append(
        violinMark({
          center,
          halfLength: violinHalfLength(profile, medianSeconds, maxHalf),
          level: y(medianSeconds),
          className: "violin-median",
          label: `${group.label}: ${integerFormatter.format(values.length)} finishes by ${integerFormatter.format(racers)} racers; median ${formatDuration(medianSeconds)}; middle half ${formatDuration(stats.q1)}–${formatDuration(stats.q3)}`,
        }),
      );
    }
    violins.push(violin);
    const label = (row, className, text) =>
      svgElement("text", { class: `axis-label ${className}`, x: center, y: labelRow(row), "text-anchor": "middle" }, text);
    labels.push(
      svgElement("circle", { class: "violin-swatch", cx: center, cy: plotBottom + 12, r: 5, style: color }),
      ...nameLines[groupIndex].map((line, row) => label(row, "axis-label--mode", line)),
    );
    if (hasModeLine) {
      labels.push(label(nameRows, "axis-label--violin-mode", group.modeLine));
    }
    labels.push(label(nameRows + Number(hasModeLine), "axis-label--sample", `${integerFormatter.format(values.length)} FINISHES`));
  });
  svg.append(...violins, ...labels);

  // One delegated handler: a chart can draw thousands of dots.
  svg.addEventListener("mousemove", (event) => {
    if (tapEchoing()) {
      return;
    }
    const dot = event.target.closest("circle[data-i]");
    if (dot) {
      showTooltip(dot, violinPointLabel(finishes[dot.dataset.i], groups[dot.dataset.g].label), event);
      return;
    }
    const summary = event.target.closest(".violin-summary");
    if (summary) {
      showTooltip(summary, summary.getAttribute("aria-label"), event);
    } else {
      hideTooltip();
    }
  });
  svg.addEventListener("mouseleave", () => {
    if (!tapEchoing()) {
      hideTooltip();
    }
  });
  // Touch: a tap on a median or gold line reads that line. A dot is too small to hit, so any other
  // tap reads the nearest dot within reach. No scrubbing: the chart scrolls sideways on narrow screens.
  svg.addEventListener("pointerup", (event) => {
    if (!isTouchPointer(event)) {
      return;
    }
    const summary = event.target.closest(".violin-summary");
    if (summary) {
      showTouchTooltip(summary, summary.getAttribute("aria-label"), svg);
      return;
    }
    const bounds = svg.getBoundingClientRect();
    const scale = svg.viewBox.baseVal.width / bounds.width;
    const x = (event.clientX - bounds.left) * scale;
    const y = (event.clientY - bounds.top) * scale;
    let nearest = null;
    let nearestDistance = VIOLIN_TAP_REACH * scale;
    for (const dot of svg.querySelectorAll("circle[data-i]")) {
      const distance = Math.hypot(Number(dot.getAttribute("cx")) - x, Number(dot.getAttribute("cy")) - y);
      if (distance < nearestDistance) {
        nearest = dot;
        nearestDistance = distance;
      }
    }
    if (nearest) {
      showTouchTooltip(nearest, violinPointLabel(finishes[nearest.dataset.i], groups[nearest.dataset.g].label), svg);
    } else {
      hideTooltip();
    }
  });
  return svg;
}

function setNote(selector, text) {
  const note = document.querySelector(selector);
  note.hidden = text === null;
  note.textContent = text ?? "";
}

const drawnChartKeys = new Map();

// True when the host already shows this key, so a redraw that would change nothing (a table sort) can be skipped.
function isChartCurrent(host, key) {
  if (drawnChartKeys.get(host) === key) {
    return true;
  }
  drawnChartKeys.set(host, key);
  return false;
}

// The presets a violin chart shows: those with enough races for a shape, the first VIOLIN_SLOTS by the
// caller's ranking, then laid out in mode order, which keeps neighbouring mode colours colour-blind safe.
function chartedPresets(presetIndexes, compare) {
  return presetIndexes
    .filter((presetIndex) => participants.presets[presetIndex].races >= VIOLIN_MIN_PRESET_RACES)
    .sort(compare)
    .slice(0, VIOLIN_SLOTS)
    .sort((left, right) => participants.presets[left].m - participants.presets[right].m);
}

// One violin per charted preset, from every timed finish in the archive. The median and gold lines use
// the published preset statistics, which come from exact times, so they match the presets table.
function renderPresetViolins(host, presetIndexes, { id, highlightPlayer, title, description, emptyText }) {
  const key = [highlightPlayer, title, ...presetIndexes].join("|");
  if (isChartCurrent(host, key)) {
    return;
  }
  const slots = new Map(presetIndexes.map((presetIndex, slot) => [presetIndex, slot]));
  const finishes = timedFinishes(ALL_RACES, () => true).filter((finish) => slots.has(participants.races[finish.raceIndex].p));
  if (!finishes.length) {
    mountChart(host, () => emptyMessage(emptyText));
    return;
  }
  const indexesBySlot = presetIndexes.map(() => []);
  finishes.forEach((finish, index) => indexesBySlot[slots.get(participants.races[finish.raceIndex].p)].push(index));
  const mixesModes = new Set(presetIndexes.map((presetIndex) => participants.presets[presetIndex].m)).size > 1;
  const groups = presetIndexes.map((presetIndex, slot) => {
    const preset = participants.presets[presetIndex];
    const modeName = participants.modes[preset.m];
    const indexes = indexesBySlot[slot];
    const wins = indexes.filter((index) => finishes[index].place === 1).length;
    return {
      label: `${modeLabel(modeName)} · ${preset.n}`,
      name: preset.n,
      modeLine: mixesModes ? modeLabel(modeName).toUpperCase() : null,
      color: modeColor(modeName),
      indexes,
      median: preset.median_seconds,
      gold: preset.median_win_seconds === null ? null : { median: preset.median_win_seconds, wins },
    };
  });
  const draw = () =>
    mountChart(host, (width) => violinChart(finishes, groups, { id, highlightPlayer, title, description }, width), VIOLIN_MIN_WIDTH);
  draw();
  // The labels are measured on a canvas, which measures a fallback font until the page font has loaded.
  const font = axisLabelFont();
  if (!document.fonts.check(font)) {
    document.fonts
      .load(font)
      .then(() => {
        if (drawnChartKeys.get(host) === key) {
          draw();
        }
      })
      .catch((error) => console.error("Could not load the violin label font.", error));
  }
}

// Columns of the presets table under each preset violin chart. A row is { preset, index, rank }: preset is
// shaped like a participants.presets entry and index is its preset index. fastestLink(best) gives the node
// after the fastest time, from best = [player, seconds, raceIndex]: the racer in Hall of records, the race
// on a racer page. Mixing modes adds a Mode column, so color is never the only cue.
function presetColumns({ showMode = false, fastestLink }) {
  const modeColumn = {
    key: "mode",
    label: "Mode",
    align: "left",
    defaultDirection: "asc",
    sortValue: (row) => modeLabel(participants.modes[row.preset.m]),
    render: (cell, row) => {
      const modeName = participants.modes[row.preset.m];
      cell.append(UI.modeDot(modeName), " ", modeLabel(modeName));
    },
  };
  return [
    {
      key: "preset",
      label: "Preset",
      align: "left",
      defaultDirection: "asc",
      sortValue: (row) => row.preset.n,
      render: (cell, row) => {
        const name = document.createElement("span");
        name.className = "preset-name";
        name.textContent = row.preset.n;
        cell.appendChild(name);
        if (row.index === participants.hardest_preset) {
          cell.append(" ", UI.tag("hardest", "Hardest", `Highest DNF rate among presets with ${participants.preset_min_races}+ races`));
        }
      },
    },
    ...(showMode ? [modeColumn] : []),
    {
      key: "races",
      label: "Races",
      sortValue: (row) => row.preset.races,
      render: (cell, row) => cell.append(String(row.preset.races)),
    },
    {
      key: "median",
      label: "Median time",
      defaultDirection: "asc",
      sortValue: (row) => row.preset.median_seconds,
      render: (cell, row) => cell.append(UI.time(row.preset.median_seconds)),
    },
    {
      key: "gold",
      label: "Gold median",
      sortLabel: "median winning time",
      defaultDirection: "asc",
      sortValue: (row) => row.preset.median_win_seconds,
      render: (cell, row) => cell.append(UI.time(row.preset.median_win_seconds)),
    },
    {
      key: "dnf",
      label: "DNF %",
      sortLabel: "DNF rate",
      sortValue: (row) => row.preset.dnf_rate,
      render: (cell, row) => cell.append(formatRate(row.preset.dnf_rate)),
    },
    {
      key: "field",
      label: "Avg racers",
      sortValue: (row) => row.preset.mean_field,
      render: (cell, row) => cell.append(row.preset.mean_field.toFixed(1)),
    },
    {
      key: "fastest",
      label: "Fastest",
      sortLabel: "fastest time",
      defaultDirection: "asc",
      sortValue: (row) => (row.preset.best ? row.preset.best[1] : null),
      render: (cell, row) => {
        if (!row.preset.best) {
          cell.append("--");
          return;
        }
        cell.append(UI.time(row.preset.best[1]), " ", fastestLink(row.preset.best));
      },
    },
  ];
}

// The racer's own results in each preset they entered, in one mode or, with modeIndex null, every mode.
// Each value is shaped and defined like the preset statistics in scripts/highlights.py, over the racer's
// entries only, so presetColumns can show them.
function playerPresetStats(playerIndex, modeIndex) {
  const tallies = new Map();
  raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
    const race = participants.races[raceIndex];
    if (race.p === null || (modeIndex !== null && race.m !== modeIndex)) {
      return;
    }
    if (!tallies.has(race.p)) {
      tallies.set(race.p, { races: 0, dnfs: 0, fields: 0, times: [], wins: [], best: null });
    }
    const tally = tallies.get(race.p);
    const entry = race.e.find((item) => item[0] === playerIndex);
    tally.races += 1;
    tally.fields += race.n;
    if (!isFinisher(entry)) {
      tally.dnfs += 1;
    } else if (entry[3] !== null) {
      tally.times.push(entry[3]);
      if (entry[1] === 1) {
        tally.wins.push(entry[3]);
      }
      // Races arrive in date order, so on a tie the earliest best stands.
      if (tally.best === null || entry[3] < tally.best[1]) {
        tally.best = [playerIndex, entry[3], raceIndex];
      }
    }
  });
  const stats = new Map();
  tallies.forEach((tally, presetIndex) => {
    const preset = participants.presets[presetIndex];
    stats.set(presetIndex, {
      m: preset.m,
      n: preset.n,
      races: tally.races,
      dnf_rate: tally.dnfs / tally.races,
      median_seconds: tally.times.length ? Math.round(median(tally.times)) : null,
      median_win_seconds: tally.wins.length ? Math.round(median(tally.wins)) : null,
      mean_field: tally.fields / tally.races,
      best: tally.best,
    });
  });
  return stats;
}

// Most entered by the racer first, then most raced overall, then by name. The violins and the table share it.
function playerPresetOrder(stats) {
  const presets = participants.presets;
  return (left, right) =>
    stats.get(right).races - stats.get(left).races ||
    presets[right].races - presets[left].races ||
    presets[left].n.localeCompare(presets[right].n);
}

// The presets the racer entered most, in the picked mode or across all of them, with their finishes highlighted.
function renderPlayerViolins(playerIndex, modeId, stats) {
  const name = participants.players[playerIndex].name;
  const charted = chartedPresets([...stats.keys()], playerPresetOrder(stats));
  const where = modeId === ALL_MODES ? "" : ` in ${modeLabel(modeId)}`;
  setNote(
    "#player-violin-note",
    charted.length < stats.size
      ? `Charting ${charted.length} of the ${stats.size} presets ${name} has entered${where}: the most entered among those with ${VIOLIN_MIN_PRESET_RACES}+ races, up to ${VIOLIN_SLOTS}. The table lists them all.`
      : null,
  );
  renderPresetViolins(document.querySelector("#player-violin-chart"), charted, {
    id: "player-violin",
    highlightPlayer: playerIndex,
    title: `${name}'s most-entered presets${where}`,
    description: `Every finish in the presets ${name} has entered most${where}, with ${name}'s finishes highlighted, the bold line marking the median and the gold line the median winning time.`,
    emptyText: `${name} has entered no preset${where} with ${VIOLIN_MIN_PRESET_RACES}+ races.`,
  });
}

function raceDateLink([, , raceIndex]) {
  const race = participants.races[raceIndex];
  const link = document.createElement("a");
  link.href = `${RACE_ROOT}${race.r}`;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = formatRaceDate(race.d);
  return link;
}

// Every preset the racer entered, with their own results. A sort redraws only the table.
function renderPlayerPresetTable(playerIndex, modeId, stats) {
  const host = document.querySelector("#player-preset-table");
  if (!stats.size) {
    const where = modeId === ALL_MODES ? "" : ` in ${modeLabel(modeId)}`;
    host.replaceChildren(emptyMessage(`${participants.players[playerIndex].name} has entered no preset${where}.`));
    return;
  }
  const rows = [...stats.keys()]
    .sort(playerPresetOrder(stats))
    .map((presetIndex, rank) => ({ preset: stats.get(presetIndex), index: presetIndex, rank }));
  const columns = presetColumns({ showMode: modeId === ALL_MODES, fastestLink: raceDateLink });
  const state = { sort: playerState.presetSort ?? PLAYER_PRESET_SORT };
  renderSortableTable(host, columns, sortRows(rows, columns, state.sort), state, (column) => {
    playerState.presetSort = nextSort(state, column);
    renderPlayerPresetTable(playerIndex, modeId, stats);
  });
}

function renderPlayerCharts(playerIndex) {
  const modeId = playerState.mode;
  const stats = playerPresetStats(playerIndex, modeId === ALL_MODES ? null : modeIndexFor(modeId));
  document.querySelector("#player-legend-name").textContent = participants.players[playerIndex].name;
  renderPlayerViolins(playerIndex, modeId, stats);
  if (typeof UI !== "undefined") {
    renderPlayerPresetTable(playerIndex, modeId, stats);
  }
}
