const SVG_NS = "http://www.w3.org/2000/svg";
const tooltip = document.querySelector("#chart-tooltip");
const integerFormatter = new Intl.NumberFormat("en-US");
const decimalFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
// Narrowest width at which the monthly line charts and season bars keep legible labels; narrower hosts scroll.
const MIN_CHART_WIDTH = 260;
// Each nice axis maximum as a multiple of its power of ten, with a tick count that keeps every tick round.
const NICE_SCALES = [
  [1, 5], [1.2, 4], [1.5, 5], [2, 4], [2.5, 5], [3, 3], [4, 4], [5, 5], [6, 6], [8, 4], [10, 5],
];
// Month labels thin out by divisors of 12, so January always keeps its label.
const MONTH_LABEL_STEPS = [1, 2, 3, 6, 12];
const MONTH_LABEL_MIN_SPACING = 40;
const chartBuilders = new Map();
const chartWidths = new Map();
const chartResizeObserver = new ResizeObserver((entries) => {
  requestAnimationFrame(() => entries.forEach((entry) => drawChart(entry.target)));
});

function svgElement(name, attributes = {}, text = null) {
  const element = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  if (text !== null) {
    element.textContent = text;
  }
  return element;
}

function compactHour(hour) {
  if (hour === 0) return "12a";
  if (hour === 12) return "12p";
  return hour < 12 ? `${hour}a` : `${hour - 12}p`;
}

function fullHour(hour) {
  if (hour === 0) return "12 AM";
  if (hour === 12) return "12 PM";
  return hour < 12 ? `${hour} AM` : `${hour - 12} PM`;
}

function hourRange(hour) {
  return `${fullHour(hour)}-${fullHour((hour + 1) % 24)}`;
}

// Mode ids are snake_case; labels read in sentence case, like "All modes".
function formatModeName(mode) {
  const words = mode.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function utcTimestamp(isoString) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(isoString));
}

// "just now" under a minute, then whole minutes, hours or days, rounded down.
function timeAgo(isoString) {
  const minutes = Math.floor((Date.now() - Date.parse(isoString)) / 60000);
  if (minutes < 1) return "just now";
  const format = new Intl.RelativeTimeFormat("en", { numeric: "always" });
  if (minutes < 60) return format.format(-minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return format.format(-hours, "hour");
  return format.format(-Math.floor(hours / 24), "day");
}

function niceScale(value) {
  if (!Number.isFinite(value) || value <= 0) return { maximum: 1, tickCount: 5 };
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const [step, tickCount] = NICE_SCALES.find(([candidate]) => normalized <= candidate + 1e-9);
  return { maximum: Number((step * magnitude).toPrecision(6)), tickCount };
}

// The bounding width keeps its fraction. clientWidth rounds, and rounding up drew a chart up to half a
// pixel wider than its host, which then showed a scrollbar. Hidden hosts measure 0.
function hostWidth(host) {
  const style = getComputedStyle(host);
  return host.getBoundingClientRect().width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
}

// Charts draw at their host's pixel width so SVG text keeps its stated size in every panel.
function drawChart(host) {
  const available = hostWidth(host);
  if (available <= 0) return; // Hidden; the resize observer draws it once it is shown.
  const { build, minWidth } = chartBuilders.get(host);
  const width = Math.max(minWidth, Math.floor(available));
  if (chartWidths.get(host) === width) return;
  chartWidths.set(host, width);
  host.replaceChildren(build(width));
}

function mountChart(host, build, minWidth = MIN_CHART_WIDTH) {
  chartBuilders.set(host, { build, minWidth });
  chartWidths.delete(host);
  chartResizeObserver.observe(host);
  drawChart(host);
}

function formatAverage(value) {
  return value === null ? "--" : decimalFormatter.format(value);
}

// Whole numbers print as integers, anything else with one decimal.
function formatCount(value) {
  return Number.isInteger(value) ? integerFormatter.format(value) : decimalFormatter.format(value);
}

function formatPercent(value) {
  return `${integerFormatter.format(Math.round(value * 100))}%`;
}

// Sits above the anchor; with no room there it drops below anchorBottom instead of covering it,
// and either way stays inside the viewport.
function positionTooltip(clientX, clientY, anchorBottom = clientY + 8) {
  const margin = 12;
  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  const left = Math.min(window.innerWidth - width - margin, Math.max(margin, clientX + 10));
  const above = clientY - height - 12;
  const preferred = above >= margin ? above : anchorBottom + 12;
  const top = Math.max(margin, Math.min(preferred, window.innerHeight - height - margin));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function showTooltip(target, text, event = null) {
  tooltip.textContent = text;
  tooltip.hidden = false;
  if (event && "clientX" in event) {
    positionTooltip(event.clientX, event.clientY);
    return;
  }
  const bounds = target.getBoundingClientRect();
  positionTooltip(bounds.left + bounds.width / 2, bounds.top, bounds.bottom);
}

function hideTooltip() {
  tooltip.hidden = true;
  touchTooltipOwner = null;
}

// Escape dismisses the tooltip wherever it came from, so it never has to be moused away.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    hideTooltip();
  }
});

// Touch: a tap shows what a mouse would see on hover. Only touch and pen pointers take this path,
// so with a mouse every tooltip behaves exactly as before.
// The element whose tooltip a tap opened (for a chart, the chart), or null. A touch outside it, or
// any scroll, closes that tooltip: it is fixed to the screen and would otherwise point at nothing.
let touchTooltipOwner = null;
// After a tap the browser replays it as mouse and focus events. Within this window those events
// belong to the tap, so the hover and focus handlers leave the tooltip to the touch path.
const TAP_ECHO_MS = 1000;
let lastTouchAt = -Infinity;

function isTouchPointer(event) {
  return event.pointerType === "touch" || event.pointerType === "pen";
}

function tapEchoing() {
  return performance.now() - lastTouchAt < TAP_ECHO_MS;
}

// Shows the tooltip above target for a tap; owner is the element a later touch must stay inside.
function showTouchTooltip(target, text, owner = target) {
  showTooltip(target, text);
  touchTooltipOwner = owner;
}

// Capture phase: this runs before any handler that opens a new tooltip for the same touch, so it
// never closes the tooltip that touch is about to open.
function noteTouch(event) {
  if (!isTouchPointer(event)) {
    return;
  }
  lastTouchAt = performance.now();
  if (event.type === "pointerdown" && touchTooltipOwner && !touchTooltipOwner.contains(event.target)) {
    hideTooltip();
  }
}
document.addEventListener("pointerdown", noteTouch, true);
document.addEventListener("pointerup", noteTouch, true);
document.addEventListener("scroll", () => {
  if (touchTooltipOwner) {
    hideTooltip();
  }
}, { capture: true, passive: true });

function bindTooltip(target, text) {
  target.addEventListener("mouseenter", (event) => {
    if (!tapEchoing()) {
      showTooltip(target, text, event);
    }
  });
  target.addEventListener("mousemove", (event) => {
    if (!tapEchoing()) {
      positionTooltip(event.clientX, event.clientY);
    }
  });
  target.addEventListener("mouseleave", () => {
    if (!tapEchoing()) {
      hideTooltip();
    }
  });
  target.addEventListener("focus", () => {
    if (!tapEchoing()) {
      showTooltip(target, text);
    }
  });
  target.addEventListener("blur", () => {
    if (!tapEchoing()) {
      hideTooltip();
    }
  });
  // A link or button keeps its own action on tap, without a tooltip in the way. A second tap on
  // the same element closes its tooltip.
  target.addEventListener("pointerup", (event) => {
    if (!isTouchPointer(event) || target.closest("a[href], button, summary")) {
      return;
    }
    if (touchTooltipOwner === target && !tooltip.hidden) {
      hideTooltip();
    } else {
      showTouchTooltip(target, text);
    }
  });
}

const BAND_LABELS = { prime: "primetime", normal: "normal", quiet: "quiet" };
// Must match BAND_MIN_SCHEDULED in scripts/build_stats.py and the heatmap legend in index.html.
const BAND_MIN_SCHEDULED = 5;

function startRateText(startedCount, scheduledCount) {
  return `${integerFormatter.format(startedCount)} of ${integerFormatter.format(scheduledCount)} scheduled races started (${formatPercent(startedCount / scheduledCount)})`;
}

function renderPrimetimeHeatmap(container, items) {
  const grid = document.createElement("div");
  grid.className = "primetime-heatmap";
  grid.setAttribute("role", "group");
  grid.setAttribute("aria-label", "Average racers per scheduled race by weekday and Eastern start hour");

  const corner = document.createElement("div");
  corner.className = "heatmap-corner";
  corner.textContent = "Eastern";
  grid.append(corner);
  for (let hour = 0; hour < 24; hour += 1) {
    const header = document.createElement("div");
    header.className = "heatmap-hour";
    header.textContent = compactHour(hour);
    grid.append(header);
  }

  for (let weekday = 0; weekday < 7; weekday += 1) {
    const rowItems = items.filter((item) => item.weekday === weekday);
    const rowLabel = document.createElement("div");
    rowLabel.className = "heatmap-weekday";
    rowLabel.textContent = rowItems[0]?.weekday_label.slice(0, 3).toUpperCase() ?? "--";
    grid.append(rowLabel);

    rowItems.forEach((item) => {
      const cell = document.createElement("div");
      const place = `${item.weekday_label}, ${hourRange(item.hour)}`;
      cell.className = `heatmap-cell ${item.band ? `heatmap-cell--${item.band}` : "heatmap-cell--unbanded"}`;
      cell.tabIndex = 0;
      cell.setAttribute("role", "img");
      let label;
      if (item.scheduled_count === 0) {
        cell.textContent = "·";
        label = `${place}: no scheduled races`;
      } else {
        const detail = `${formatAverage(item.average_racers)} racers per scheduled race; ${startRateText(item.started_count, item.scheduled_count)}`;
        cell.textContent = formatAverage(item.average_racers);
        label = item.band
          ? `${place}: ${BAND_LABELS[item.band]}. ${detail}`
          : `${place}: no band, fewer than ${BAND_MIN_SCHEDULED} scheduled races. ${detail}`;
      }
      cell.setAttribute("aria-label", label);
      bindTooltip(cell, label);
      grid.append(cell);
    });
  }

  container.replaceChildren(grid);
}

function seasonBarChart(seasons, options, width) {
  const height = 350;
  const margin = { top: 30, right: 14, bottom: 48, left: 50 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const values = seasons.map((item) => options.value(item));
  const { maximum, tickCount } = options.scale
    ?? niceScale(Math.max(...values.map((value) => value ?? 0)));
  const svg = svgElement("svg", {
    class: "chart-svg",
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": `${options.id}-title ${options.id}-description`,
  });
  svg.append(
    svgElement("title", { id: `${options.id}-title` }, options.title),
    svgElement("desc", { id: `${options.id}-description` }, options.description),
  );

  for (let tick = 0; tick <= tickCount; tick += 1) {
    const value = (maximum * tick) / tickCount;
    const y = margin.top + plotHeight - (value / maximum) * plotHeight;
    svg.append(
      svgElement("line", {
        class: tick === 0 ? "baseline" : "grid-line",
        x1: margin.left,
        x2: width - margin.right,
        y1: y,
        y2: y,
      }),
      svgElement(
        "text",
        { class: "axis-label", x: margin.left - 8, y: y + 4, "text-anchor": "end" },
        options.axisFormatter(value),
      ),
    );
  }

  const bandWidth = plotWidth / seasons.length;
  const barWidth = bandWidth * 0.62;
  seasons.forEach((item, index) => {
    const value = values[index];
    const x = margin.left + index * bandWidth + (bandWidth - barWidth) / 2;
    if (value !== null) {
      const barHeight = (value / maximum) * plotHeight;
      const y = margin.top + plotHeight - barHeight;
      const label = options.tooltip(item);
      const group = svgElement("g", {
        class: "chart-mark",
        tabindex: "0",
        role: "img",
        "aria-label": label,
      });
      const bar = svgElement("rect", {
        class: item.is_complete ? "bar bar--series" : "bar bar--muted",
        x,
        y,
        width: barWidth,
        height: Math.max(0, barHeight),
        rx: 1,
      });
      const focusRing = svgElement("rect", {
        class: "focus-ring",
        x: x - 2,
        y: Math.max(margin.top, y - 2),
        width: barWidth + 4,
        height: Math.max(4, barHeight + 4),
        rx: 2,
      });
      group.append(bar, focusRing, svgElement("title", {}, label));
      bindTooltip(group, label);
      svg.append(group);
    }
    svg.append(svgElement(
      "text",
      { class: "axis-label", x: x + barWidth / 2, y: height - 25, "text-anchor": "middle" },
      String(item.season),
    ));
  });

  svg.append(
    svgElement(
      "text",
      { class: "axis-label", x: margin.left + plotWidth / 2, y: height - 5, "text-anchor": "middle" },
      "SEASON",
    ),
  );
  return svg;
}

function monthTitle(month) {
  return `${month.label}${month.is_complete ? "" : " (partial)"}`;
}

function monthlyLineChart(months, selectedPeriodId, options, width) {
  const height = 360;
  const margin = { top: 24, right: 20, bottom: 60, left: 68 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const values = months.map((month) => month[options.valueKey]);
  const { maximum, tickCount } = niceScale(Math.max(...values.filter((value) => value !== null)));
  const step = plotWidth / months.length;
  const x = (index) => margin.left + (index + 0.5) * step;
  const y = (value) => margin.top + plotHeight - (value / maximum) * plotHeight;
  const svg = svgElement("svg", {
    class: "chart-svg",
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": `${options.id}-title ${options.id}-description`,
  });
  svg.append(
    svgElement("title", { id: `${options.id}-title` }, options.title),
    svgElement("desc", { id: `${options.id}-description` }, options.description),
  );

  // A selected quarter shades its months instead of hiding the rest of the trend.
  const selectedIndexes = months.flatMap((month, index) => (month.quarter_id === selectedPeriodId ? [index] : []));
  if (selectedIndexes.length) {
    svg.append(svgElement("rect", {
      class: "period-highlight",
      x: margin.left + selectedIndexes[0] * step,
      y: margin.top,
      width: selectedIndexes.length * step,
      height: plotHeight,
    }));
  }

  for (let tick = 0; tick <= tickCount; tick += 1) {
    const value = (maximum * tick) / tickCount;
    svg.append(
      svgElement("line", {
        class: tick === 0 ? "baseline" : "grid-line",
        x1: margin.left,
        x2: width - margin.right,
        y1: y(value),
        y2: y(value),
      }),
      svgElement(
        "text",
        { class: "axis-label", x: margin.left - 10, y: y(value) + 4, "text-anchor": "end" },
        options.axisFormatter(value),
      ),
    );
  }

  // A month without races has no average, so the line breaks there instead of bridging the gap.
  const path = values
    .map((value, index) => {
      if (value === null) return "";
      const command = index > 0 && values[index - 1] !== null ? "L" : "M";
      return `${command}${x(index)},${y(value)}`;
    })
    .join("");
  svg.append(svgElement("path", { class: "turnout-line", d: path }));

  const labelStep = MONTH_LABEL_STEPS.find((candidate) => candidate * step >= MONTH_LABEL_MIN_SPACING) ?? 12;
  let labelledYear = null;
  months.forEach((month, index) => {
    const value = values[index];
    if (value !== null) {
      const label = options.tooltip(month);
      const group = svgElement("g", {
        class: "chart-mark",
        tabindex: "0",
        role: "img",
        "aria-label": label,
      });
      group.append(
        svgElement("rect", {
          class: "turnout-hit",
          x: x(index) - step / 2,
          y: margin.top,
          width: step,
          height: plotHeight,
        }),
        svgElement("line", {
          class: "turnout-guide",
          x1: x(index),
          x2: x(index),
          y1: margin.top,
          y2: margin.top + plotHeight,
        }),
        svgElement("circle", { class: "turnout-dot", cx: x(index), cy: y(value), r: 4 }),
        svgElement("title", {}, label),
      );
      bindTooltip(group, label);
      svg.append(group);
    }

    if ((Number(month.id.slice(5)) - 1) % labelStep !== 0) return;
    const [monthName, year] = month.label.split(" ");
    svg.append(svgElement(
      "text",
      { class: "axis-label axis-label--period", x: x(index), y: height - 34, "text-anchor": "middle" },
      monthName.toUpperCase(),
    ));
    if (year !== labelledYear) {
      svg.append(svgElement(
        "text",
        { class: "axis-label", x: x(index), y: height - 19, "text-anchor": "middle" },
        year,
      ));
      labelledYear = year;
    }
  });

  svg.append(
    svgElement(
      "text",
      { class: "axis-label", x: 14, y: margin.top + plotHeight / 2, transform: `rotate(-90 14 ${margin.top + plotHeight / 2})`, "text-anchor": "middle" },
      options.axisTitle,
    ),
  );
  return svg;
}

function renderTable(container, headers, rows) {
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headers.forEach((header) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = header;
    headRow.append(cell);
  });
  head.append(headRow);

  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tableRow = document.createElement("tr");
    row.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      tableRow.append(cell);
    });
    body.append(tableRow);
  });
  table.append(head, body);
  container.replaceChildren(table);
}

const BANDS = new Set(["prime", "normal", "quiet"]);

function hasValidSeasons(seasons) {
  return (
    Array.isArray(seasons)
    && seasons.length > 0
    && seasons.every((item, index) => (
      Number.isInteger(item.season)
      && (index === 0 || item.season > seasons[index - 1].season)
      && typeof item.is_complete === "boolean"
      && Number.isFinite(item.scheduled_count) && item.scheduled_count >= 0
      && Number.isFinite(item.started_count) && item.started_count >= 0
      && Number.isFinite(item.race_count) && item.race_count >= 0
      && Number.isFinite(item.entrant_total) && item.entrant_total >= 0
      && item.started_count <= item.scheduled_count
    ))
  );
}

function hasValidAggregate(aggregate) {
  return (
    Array.isArray(aggregate?.weekday_hourly)
    && aggregate.weekday_hourly.length === 168
    && aggregate.weekday_hourly.every((item) => (
      Number.isFinite(item.weekday)
      && Number.isFinite(item.hour)
      && Number.isFinite(item.scheduled_count)
      && Number.isFinite(item.started_count)
      && (item.average_racers === null || Number.isFinite(item.average_racers))
      && (item.band === null || BANDS.has(item.band))
    ))
  );
}

function isIsoDate(value) {
  return typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

function validateData(data) {
  if (
    !data
    || data.schema_version !== 10
    || !hasValidAggregate(data)
    || !hasValidSeasons(data.seasons)
    || !Array.isArray(data.periods)
    || data.periods.length === 0
    || data.periods.some((period) => (
      typeof period.id !== "string"
      || typeof period.start !== "string"
      || !isIsoDate(period.end)
      || period.end < data.source?.data_start_local
      || !period.observed_local_date_range
      || typeof period.observed_local_date_range.start !== "string"
      || typeof period.observed_local_date_range.end !== "string"
      || !hasValidAggregate(period)
    ))
    || !Array.isArray(data.monthly)
    || data.monthly.length === 0
    || data.monthly.some((month) => (
      typeof month.id !== "string"
      || typeof month.label !== "string"
      || typeof month.quarter_id !== "string"
      || !Number.isFinite(month.race_count)
      || !Number.isFinite(month.entrant_total)
      || !(Number.isFinite(month.scheduled_day_count) && month.scheduled_day_count >= 0)
      || !Number.isFinite(month.scheduled_day_entrant_total)
      || (month.entries_per_day !== null && !Number.isFinite(month.entries_per_day))
      || !Number.isFinite(month.fired_race_count)
      || (month.fired_per_day !== null && !Number.isFinite(month.fired_per_day))
      || (month.average_racers !== null && !Number.isFinite(month.average_racers))
    ))
    || !data.source
    || !isIsoDate(data.source.data_start_local)
    || !Number.isFinite(data.source.archived_race_count)
    || !Number.isFinite(data.source.included_race_count)
    || !Number.isFinite(data.source.included_entrant_count)
    || (data.source.schedule_counted_through !== null && Number.isNaN(Date.parse(data.source.schedule_counted_through)))
  ) {
    throw new Error("The generated race dataset has an unsupported shape.");
  }
}

function allDataSelection(data) {
  return {
    id: "all",
    label: "All data",
    weekday_hourly: data.weekday_hourly,
  };
}

function renderCountedThrough(isoString) {
  const node = document.querySelector("#schedule-counted-through");
  node.hidden = isoString === null;
  if (isoString === null) return;
  const formatted = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(new Date(isoString));
  node.textContent = `Scheduled races counted through ${formatted}`;
}

function renderSelection(data, selection) {
  document.querySelector("#active-period-label").textContent = selection.label;
  renderPrimetimeHeatmap(document.querySelector("#primetime-heatmap"), selection.weekday_hourly);
  mountChart(document.querySelector("#participation-racers-chart"), (width) => monthlyLineChart(data.monthly, selection.id, {
    id: "participation-racers",
    valueKey: "average_racers",
    title: "Average racers per race by month",
    description: "A line with one dot per calendar month shows average total entrants per finished race.",
    axisTitle: "AVERAGE RACERS PER RACE",
    axisFormatter: (value) => decimalFormatter.format(value),
    tooltip: (month) => `${monthTitle(month)}: ${formatAverage(month.average_racers)} average racers across ${integerFormatter.format(month.race_count)} races`,
  }, width));
  mountChart(document.querySelector("#participation-entries-chart"), (width) => monthlyLineChart(data.monthly, selection.id, {
    id: "participation-entries",
    valueKey: "entries_per_day",
    title: "Race entries per day by month",
    description: "A line with one dot per calendar month shows recorded race entries per day with scheduled races.",
    axisTitle: "RACE ENTRIES PER DAY",
    axisFormatter: (value) => integerFormatter.format(value),
    tooltip: (month) => (month.entries_per_day === null
      ? `${monthTitle(month)}: no scheduled races`
      : `${monthTitle(month)}: ${decimalFormatter.format(month.entries_per_day)} entries per day (${integerFormatter.format(month.scheduled_day_entrant_total)} entries over ${integerFormatter.format(month.scheduled_day_count)} scheduled days)`),
  }, width));
  mountChart(document.querySelector("#participation-races-chart"), (width) => monthlyLineChart(data.monthly, selection.id, {
    id: "participation-races",
    valueKey: "fired_per_day",
    title: "Races fired per day by month",
    description: "A line with one dot per calendar month shows scheduled races that started per day with scheduled races.",
    axisTitle: "RACES FIRED PER DAY",
    axisFormatter: formatCount,
    tooltip: (month) => (month.fired_per_day === null
      ? `${monthTitle(month)}: no scheduled races`
      : `${monthTitle(month)}: ${decimalFormatter.format(month.fired_per_day)} races fired per day (${integerFormatter.format(month.fired_race_count)} fired over ${integerFormatter.format(month.scheduled_day_count)} scheduled days)`),
  }, width));

  renderTable(
    document.querySelector("#participation-table"),
    ["Month", "Coverage", "Avg entries", "Median", "95% range", "Races", "Race entries", "Scheduled days", "Entries/day", "Fired/day"],
    data.monthly.map((month) => [
      month.label,
      month.is_complete ? "Complete" : "Partial",
      formatAverage(month.average_racers),
      formatAverage(month.median_racers),
      month.ci95_low === null ? "--" : `${formatAverage(month.ci95_low)} to ${formatAverage(month.ci95_high)}`,
      integerFormatter.format(month.race_count),
      integerFormatter.format(month.entrant_total),
      integerFormatter.format(month.scheduled_day_count),
      formatAverage(month.entries_per_day),
      formatAverage(month.fired_per_day),
    ]),
  );
}

function renderSeasons(seasons) {
  const inProgress = (item) => (item.is_complete ? "" : " (in progress)");
  const startRate = (item) => (item.scheduled_count ? formatPercent(item.started_count / item.scheduled_count) : "--");
  mountChart(document.querySelector("#season-start-chart"), (width) => seasonBarChart(seasons, {
    id: "season-start",
    title: "Share of scheduled races that started by season",
    description: "Vertical bars show the share of each season's scheduled races that started. The season in progress is drawn muted.",
    value: (item) => (item.scheduled_count ? item.started_count / item.scheduled_count : null),
    scale: { maximum: 1, tickCount: 5 },
    axisFormatter: formatPercent,
    tooltip: (item) => (item.scheduled_count === 0
      ? `Season ${item.season}${inProgress(item)}: no scheduled races counted`
      : `Season ${item.season}${inProgress(item)}: ${startRateText(item.started_count, item.scheduled_count)}`),
  }, width));
  mountChart(document.querySelector("#season-entries-chart"), (width) => seasonBarChart(seasons, {
    id: "season-entries",
    title: "Race entries by season",
    description: "Vertical bars show the race entries of each season's finished races. The season in progress is drawn muted.",
    value: (item) => item.entrant_total,
    axisFormatter: (value) => integerFormatter.format(value),
    tooltip: (item) => `Season ${item.season}${inProgress(item)}: ${integerFormatter.format(item.entrant_total)} race entries over ${integerFormatter.format(item.race_count)} races (${formatAverage(item.race_count ? item.entrant_total / item.race_count : null)} per race)`,
  }, width));
  renderTable(
    document.querySelector("#season-start-table"),
    ["Season", "Scheduled", "Started", "Start rate"],
    seasons.map((item) => [
      `${item.season}${inProgress(item)}`,
      integerFormatter.format(item.scheduled_count),
      integerFormatter.format(item.started_count),
      startRate(item),
    ]),
  );
  renderTable(
    document.querySelector("#season-entries-table"),
    ["Season", "Races", "Race entries", "Avg racers per race"],
    seasons.map((item) => [
      `${item.season}${inProgress(item)}`,
      integerFormatter.format(item.race_count),
      integerFormatter.format(item.entrant_total),
      formatAverage(item.race_count ? item.entrant_total / item.race_count : null),
    ]),
  );
}

function renderPeriodControls(data) {
  const container = document.querySelector("#period-toggle");
  const selections = [allDataSelection(data), ...data.periods];
  const buttons = selections.map((selection) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "period-toggle__button";
    button.dataset.periodId = selection.id;
    button.textContent = `${selection.label}${selection.id !== "all" && !selection.is_complete ? " · partial" : ""}`;
    button.setAttribute("aria-pressed", String(selection.id === "all"));
    button.addEventListener("click", () => {
      buttons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      renderSelection(data, selection);
    });
    return button;
  });
  container.replaceChildren(...buttons);
}

function renderSnapshotAge(isoString) {
  const node = document.querySelector("#generated-at");
  node.dateTime = isoString;
  bindTooltip(node, utcTimestamp(isoString));
  const update = () => {
    node.textContent = timeAgo(isoString);
  };
  update();
  // Data commits don't reload an open page, so the age keeps counting.
  setInterval(update, 60000);
}

function renderDashboard(data) {
  renderSnapshotAge(data.generated_at);
  renderPeriodControls(data);
  renderCountedThrough(data.source.schedule_counted_through);
  renderSelection(data, allDataSelection(data));
  renderSeasons(data.seasons);
}

async function load() {
  const status = document.querySelector("#load-status");
  const dashboard = document.querySelector("#dashboard");
  try {
    const response = await fetch("data/race-stats.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Race data request failed with HTTP ${response.status}.`);
    }
    const data = await response.json();
    validateData(data);
    renderDashboard(data);
    status.hidden = true;
    dashboard.hidden = false;
  } catch (error) {
    console.error(error);
    status.classList.add("load-status--error");
    status.textContent = "Race statistics could not be loaded. Refresh the generated data and try again.";
  }
}

load();