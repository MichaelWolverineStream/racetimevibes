const SVG_NS = "http://www.w3.org/2000/svg";
const tooltip = document.querySelector("#chart-tooltip");
const integerFormatter = new Intl.NumberFormat("en-US");
const decimalFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const MINIMUM_CANDIDATE_SAMPLE = 20;

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

function scheduleDate(isoString, timeZone) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone,
  }).format(new Date(isoString));
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

function niceMaximum(value) {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const rounded = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return rounded * magnitude;
}

function formatAverage(value) {
  return value === null ? "--" : decimalFormatter.format(value);
}

function formatSigned(value) {
  if (value === null) return "--";
  return `${value > 0 ? "+" : ""}${decimalFormatter.format(value)}`;
}

function formatInterval(low, high) {
  if (low === null || high === null) return "--";
  return `${formatSigned(low)} to ${formatSigned(high)}`;
}

function positionTooltip(clientX, clientY) {
  const margin = 12;
  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  const left = Math.min(window.innerWidth - width - margin, Math.max(margin, clientX + 10));
  const top = Math.max(margin, clientY - height - 12);
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
  positionTooltip(bounds.left + bounds.width / 2, bounds.top);
}

function hideTooltip() {
  tooltip.hidden = true;
}

function bindTooltip(target, text) {
  target.addEventListener("mouseenter", (event) => showTooltip(target, text, event));
  target.addEventListener("mousemove", (event) => positionTooltip(event.clientX, event.clientY));
  target.addEventListener("mouseleave", hideTooltip);
  target.addEventListener("focus", () => showTooltip(target, text));
  target.addEventListener("blur", hideTooltip);
}

function evidenceLabel(raceCount) {
  if (raceCount >= 50) return "High sample";
  if (raceCount >= 30) return "Moderate sample";
  return "Minimum sample";
}

function renderCandidateTable(container, items) {
  const candidates = items
    .filter((item) => (
      item.race_count >= MINIMUM_CANDIDATE_SAMPLE
      && item.adjusted_average !== null
    ))
    .sort((left, right) => (
      right.adjusted_average - left.adjusted_average
      || right.race_count - left.race_count
      || left.weekday - right.weekday
      || left.hour - right.hour
    ))
    .slice(0, 10);

  if (candidates.length === 0) {
    const emptyState = document.createElement("p");
    emptyState.className = "candidate-empty";
    emptyState.textContent = "No weekday-hour window has at least 20 races in this period. Use the opportunity map for exploratory signals or return to All data for ranked candidates.";
    container.replaceChildren(emptyState);
    return;
  }

  renderTable(
    container,
    ["Candidate window", "Adjusted lift", "95% range", "Raw average", "Median", "Races", "Evidence"],
    candidates.map((item) => [
      `${item.weekday_label}, ${fullHour(item.hour)}`,
      formatSigned(item.adjusted_average),
      formatInterval(item.adjusted_ci95_low, item.adjusted_ci95_high),
      formatAverage(item.average_racers),
      formatAverage(item.median_racers),
      integerFormatter.format(item.race_count),
      evidenceLabel(item.race_count),
    ]),
  );
}

function renderOpportunityHeatmap(container, items) {
  const observedValues = items
    .map((item) => item.adjusted_average)
    .filter((value) => value !== null);
  const maximumMagnitude = Math.max(1, ...observedValues.map((value) => Math.abs(value)));
  const grid = document.createElement("div");
  grid.className = "opportunity-heatmap";
  grid.setAttribute("role", "group");
  grid.setAttribute("aria-label", "Mode and period adjusted entry lift by weekday and Eastern start hour");

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
      const isReliable = item.race_count >= MINIMUM_CANDIDATE_SAMPLE;
      const value = item.adjusted_average;
      const strength = value === null ? 0 : Math.abs(value) / maximumMagnitude;
      const alpha = value === null ? 0 : (0.13 + strength * 0.72) * (isReliable ? 1 : 0.48);
      cell.className = "heatmap-cell";
      if (!isReliable) cell.classList.add("heatmap-cell--limited");
      if (strength > 0.58 && isReliable) cell.classList.add("heatmap-cell--strong");
      cell.tabIndex = 0;
      cell.setAttribute("role", "img");
      cell.style.backgroundColor = value === null
        ? "transparent"
        : value >= 0
          ? `rgba(18, 99, 74, ${alpha})`
          : `rgba(200, 74, 62, ${alpha})`;
      cell.textContent = item.race_count === 0 ? "·" : formatSigned(value);
      const label = item.race_count === 0
        ? `${item.weekday_label}, ${hourRange(item.hour)}: no observed races`
        : `${item.weekday_label}, ${hourRange(item.hour)}: ${formatSigned(value)} adjusted entries versus the matching mode-period baseline; ${formatAverage(item.average_racers)} raw average; ${integerFormatter.format(item.race_count)} races; adjusted 95% range ${formatInterval(item.adjusted_ci95_low, item.adjusted_ci95_high)}`;
      cell.setAttribute("aria-label", label);
      bindTooltip(cell, label);
      grid.append(cell);
    });
  }

  container.replaceChildren(grid);
}

function renderVerticalChart(container, items, options) {
  const width = 720;
  const height = 350;
  const margin = { top: 30, right: 14, bottom: 48, left: 50 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const maximum = niceMaximum(Math.max(...items.map((item) => item[options.valueKey] ?? 0)));
  const svg = svgElement("svg", {
    class: "chart-svg",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": `${options.id}-title ${options.id}-description`,
  });
  svg.append(
    svgElement("title", { id: `${options.id}-title` }, options.title),
    svgElement("desc", { id: `${options.id}-description` }, options.description),
  );

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = (maximum * tick) / 4;
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

  const bandWidth = plotWidth / items.length;
  const barWidth = bandWidth * 0.62;
  items.forEach((item, index) => {
    const value = item[options.valueKey] ?? 0;
    const barHeight = (value / maximum) * plotHeight;
    const x = margin.left + index * bandWidth + (bandWidth - barWidth) / 2;
    const y = margin.top + plotHeight - barHeight;
    const label = options.tooltip(item);
    const group = svgElement("g", {
      class: "chart-mark",
      tabindex: "0",
      role: "img",
      "aria-label": label,
    });
    const bar = svgElement("rect", {
      class: `bar ${options.barClass}`,
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
    svg.append(
      group,
      svgElement(
        "text",
        {
          class: "axis-label",
          x: x + barWidth / 2,
          y: height - 25,
          "text-anchor": "middle",
        },
        compactHour(item.hour),
      ),
    );
  });

  svg.append(
    svgElement(
      "text",
      { class: "axis-label", x: margin.left + plotWidth / 2, y: height - 5, "text-anchor": "middle" },
      "START HOUR / EASTERN TIME",
    ),
  );
  container.replaceChildren(svg);
}

function renderParticipationChart(container, periods, selectedPeriodId) {
  const width = 900;
  const height = 360;
  const margin = { top: 38, right: 20, bottom: 68, left: 68 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const periodAverages = periods.map((period) => period.entrant_total / period.race_count);
  const maximum = niceMaximum(Math.max(...periodAverages));
  const svg = svgElement("svg", {
    class: "chart-svg chart-svg--periods",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": "participation-chart-title participation-chart-description",
  });
  svg.append(
    svgElement("title", { id: "participation-chart-title" }, "Average entries per race by six-month period"),
    svgElement(
      "desc",
      { id: "participation-chart-description" },
      "Vertical bars compare average total entrants per finished race across calendar half-years.",
    ),
  );

  for (let tick = 0; tick <= 4; tick += 1) {
    const value = (maximum * tick) / 4;
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
        { class: "axis-label", x: margin.left - 10, y: y + 4, "text-anchor": "end" },
        decimalFormatter.format(value),
      ),
    );
  }

  const bandWidth = plotWidth / periods.length;
  const barWidth = Math.min(116, bandWidth * 0.58);
  periods.forEach((period, index) => {
    const average = period.entrant_total / period.race_count;
    const barHeight = (average / maximum) * plotHeight;
    const x = margin.left + index * bandWidth + (bandWidth - barWidth) / 2;
    const y = margin.top + plotHeight - barHeight;
    const label = `${period.label}${period.is_complete ? "" : " (partial)"}: ${integerFormatter.format(period.entrant_total)} race entries across ${integerFormatter.format(period.race_count)} races (${decimalFormatter.format(average)} average)`;
    const isActive = selectedPeriodId === "all" || selectedPeriodId === period.id;
    const group = svgElement("g", {
      class: "chart-mark",
      tabindex: "0",
      role: "img",
      "aria-label": label,
    });
    group.append(
      svgElement("rect", {
        class: `bar ${isActive ? "bar--blue" : "bar--muted"}`,
        x,
        y,
        width: barWidth,
        height: barHeight,
        rx: 1,
      }),
      svgElement("rect", {
        class: "focus-ring",
        x: x - 2,
        y: y - 2,
        width: barWidth + 4,
        height: barHeight + 4,
        rx: 2,
      }),
      svgElement("title", {}, label),
    );
    bindTooltip(group, label);
    const [range, year] = period.label.split(" ");
    svg.append(
      group,
      svgElement(
        "text",
        {
          class: "value-label",
          x: x + barWidth / 2,
          y: Math.max(margin.top - 8, y - 8),
          "text-anchor": "middle",
        },
        decimalFormatter.format(average),
      ),
      svgElement(
        "text",
        { class: "axis-label axis-label--period", x: x + barWidth / 2, y: height - 34, "text-anchor": "middle" },
        range.toUpperCase(),
      ),
      svgElement(
        "text",
        { class: "axis-label", x: x + barWidth / 2, y: height - 19, "text-anchor": "middle" },
        year,
      ),
    );
  });

  svg.append(
    svgElement(
      "text",
      { class: "axis-label", x: 14, y: margin.top + plotHeight / 2, transform: `rotate(-90 14 ${margin.top + plotHeight / 2})`, "text-anchor": "middle" },
      "AVERAGE ENTRIES PER RACE",
    ),
  );
  container.replaceChildren(svg);
}

function renderModeChart(container, items) {
  const width = 900;
  const rowHeight = 48;
  const margin = { top: 22, right: 74, bottom: 42, left: 175 };
  const height = margin.top + margin.bottom + items.length * rowHeight;
  const plotWidth = width - margin.left - margin.right;
  const maximum = niceMaximum(Math.max(...items.map((item) => item.average_racers ?? 0)));
  const svg = svgElement("svg", {
    class: "chart-svg chart-svg--modes",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": "mode-chart-title mode-chart-description",
  });
  svg.append(
    svgElement("title", { id: "mode-chart-title" }, "Average entries per mode"),
    svgElement(
      "desc",
      { id: "mode-chart-description" },
      "Horizontal bars compare average total entrants per finished race for each mode.",
    ),
  );

  for (let tick = 0; tick <= 5; tick += 1) {
    const value = (maximum * tick) / 5;
    const x = margin.left + (value / maximum) * plotWidth;
    svg.append(
      svgElement("line", {
        class: tick === 0 ? "baseline" : "grid-line",
        x1: x,
        x2: x,
        y1: margin.top,
        y2: height - margin.bottom,
      }),
      svgElement(
        "text",
        { class: "axis-label", x, y: height - 17, "text-anchor": "middle" },
        decimalFormatter.format(value),
      ),
    );
  }

  items.forEach((item, index) => {
    const y = margin.top + index * rowHeight + 9;
    const barHeight = 24;
    const barWidth = ((item.average_racers ?? 0) / maximum) * plotWidth;
    const modeName = item.mode.replaceAll("_", " ");
    const label = `${modeName}: ${decimalFormatter.format(item.average_racers)} average entries across ${integerFormatter.format(item.race_count)} races`;
    const group = svgElement("g", {
      class: "chart-mark",
      tabindex: "0",
      role: "img",
      "aria-label": label,
    });
    group.append(
      svgElement("rect", {
        class: "bar bar--green",
        x: margin.left,
        y,
        width: barWidth,
        height: barHeight,
        rx: 1,
      }),
      svgElement("rect", {
        class: "focus-ring",
        x: margin.left - 2,
        y: y - 2,
        width: barWidth + 4,
        height: barHeight + 4,
        rx: 2,
      }),
      svgElement("title", {}, label),
    );
    bindTooltip(group, label);
    svg.append(
      group,
      svgElement(
        "text",
        { class: "axis-label axis-label--mode", x: margin.left - 12, y: y + 11, "text-anchor": "end" },
        modeName.toUpperCase(),
      ),
      svgElement(
        "text",
        { class: "axis-label axis-label--sample", x: margin.left - 12, y: y + 24, "text-anchor": "end" },
        `${integerFormatter.format(item.race_count)} RACES`,
      ),
      svgElement(
        "text",
        { class: "value-label", x: margin.left + barWidth + 8, y: y + 16 },
        decimalFormatter.format(item.average_racers),
      ),
    );
  });

  svg.append(
    svgElement(
      "text",
      {
        class: "axis-label",
        x: margin.left + plotWidth / 2,
        y: height - 2,
        "text-anchor": "middle",
      },
      "AVERAGE TOTAL ENTRANTS PER FINISHED RACE",
    ),
  );
  container.replaceChildren(svg);
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

function validateData(data) {
  if (
    !data
    || data.schema_version !== 3
    || !Array.isArray(data.hourly)
    || data.hourly.length !== 24
    || !Array.isArray(data.weekdays)
    || data.weekdays.length !== 7
    || !Array.isArray(data.weekday_hourly)
    || data.weekday_hourly.length !== 168
    || !Array.isArray(data.modes)
    || !Array.isArray(data.periods)
    || data.periods.length === 0
    || data.periods.some((period) => (
      typeof period.id !== "string"
      || !Array.isArray(period.hourly)
      || period.hourly.length !== 24
      || !Array.isArray(period.weekdays)
      || period.weekdays.length !== 7
      || !Array.isArray(period.weekday_hourly)
      || period.weekday_hourly.length !== 168
      || !Array.isArray(period.modes)
    ))
    || !data.source
  ) {
    throw new Error("The generated race dataset has an unsupported shape.");
  }
}

function allDataSelection(data) {
  return {
    id: "all",
    label: "All data",
    race_count: data.source.included_race_count,
    entrant_total: data.source.included_entrant_count,
    included_date_range: data.source.included_date_range,
    hourly: data.hourly,
    weekdays: data.weekdays,
    weekday_hourly: data.weekday_hourly,
    modes: data.modes,
  };
}

function renderSummary(data, selection) {
  const dateRange = selection.included_date_range;
  const peakHour = selection.hourly.reduce((peak, item) => (
    item.race_count > peak.race_count ? item : peak
  ));
  document.querySelector("#generated-at").textContent = utcTimestamp(data.generated_at);
  document.querySelector("#race-total").textContent = integerFormatter.format(selection.race_count);
  document.querySelector("#mode-total").textContent = integerFormatter.format(selection.modes.length);
  document.querySelector("#peak-hour").textContent = hourRange(peakHour.hour);
  document.querySelector("#date-range").textContent = dateRange
    ? `${scheduleDate(dateRange.start, data.timezone.name)} to ${scheduleDate(dateRange.end, data.timezone.name)}`
    : "No finished races";
  document.querySelector("#active-period-label").textContent = selection.label;
}

function renderSelection(data, selection) {
  renderSummary(data, selection);
  renderCandidateTable(document.querySelector("#candidate-table"), selection.weekday_hourly);
  renderOpportunityHeatmap(
    document.querySelector("#opportunity-heatmap"),
    selection.weekday_hourly,
  );
  renderParticipationChart(
    document.querySelector("#participation-chart"),
    data.periods,
    selection.id,
  );
  renderVerticalChart(document.querySelector("#race-count-chart"), selection.hourly, {
    id: "race-count",
    valueKey: "race_count",
    title: "Race starts by hour",
    description: "Vertical bars show the number of finished races starting in each Eastern Time hour.",
    barClass: "bar--red",
    axisFormatter: (value) => integerFormatter.format(value),
    tooltip: (item) => `${hourRange(item.hour)}: ${integerFormatter.format(item.race_count)} races`,
  });
  renderVerticalChart(document.querySelector("#hourly-average-chart"), selection.hourly, {
    id: "hourly-average",
    valueKey: "average_racers",
    title: "Average entries by hour",
    description: "Vertical bars show average total entrants per finished race for each Eastern Time start hour.",
    barClass: "bar--gold",
    axisFormatter: (value) => decimalFormatter.format(value),
    tooltip: (item) => `${hourRange(item.hour)}: ${formatAverage(item.average_racers)} average entries across ${integerFormatter.format(item.race_count)} races`,
  });
  renderModeChart(document.querySelector("#mode-average-chart"), selection.modes);

  const hourlyRows = selection.hourly.map((item) => [
    hourRange(item.hour),
    integerFormatter.format(item.race_count),
    formatAverage(item.average_racers),
  ]);
  renderTable(
    document.querySelector("#participation-table"),
    ["Period", "Coverage", "Avg entries", "Median", "95% range", "Races", "Race entries"],
    data.periods.map((period) => [
      period.label,
      period.is_complete ? "Complete" : "Partial",
      formatAverage(period.average_racers),
      formatAverage(period.median_racers),
      period.ci95_low === null ? "--" : `${formatAverage(period.ci95_low)} to ${formatAverage(period.ci95_high)}`,
      integerFormatter.format(period.race_count),
      integerFormatter.format(period.entrant_total),
    ]),
  );
  renderTable(
    document.querySelector("#race-count-table"),
    ["Hour (ET)", "Races", "Avg entries"],
    hourlyRows,
  );
  renderTable(
    document.querySelector("#hourly-average-table"),
    ["Hour (ET)", "Races", "Avg entries"],
    hourlyRows,
  );
  renderTable(
    document.querySelector("#mode-average-table"),
    ["Mode", "Races", "Avg entries"],
    selection.modes.map((item) => [
      item.mode.replaceAll("_", " "),
      integerFormatter.format(item.race_count),
      decimalFormatter.format(item.average_racers),
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

function renderDashboard(data) {
  renderPeriodControls(data);
  renderSelection(data, allDataSelection(data));
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