const Records = (() => {
  const CARD_ROWS = 5;
  const MIN_ACTIVE_STREAK = 2;
  const STREAK_KINDS = [
    { kind: "daily", label: "Days in a row", unit: ["day", "days"] },
    { kind: "win", label: "Wins in a row", unit: ["win", "wins"] },
    { kind: "podium", label: "Podiums in a row", unit: ["podium", "podiums"] },
    { kind: "finish", label: "Finishes in a row", unit: ["finish", "finishes"] },
  ];
  const STREAK_VIEWS = [
    { id: "current", label: "Active now" },
    { id: "best", label: "All-time" },
  ];
  const CHART_ID = "records-progress";
  const DOT_RADIUS = 4.5;

  const state = {
    mode: null,
    streakView: "current",
    presetSort: { key: "races", direction: "desc" },
  };
  const parts = {};

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

  function plural(count, [one, many]) {
    return `${count} ${count === 1 ? one : many}`;
  }

  function mostRacedMode() {
    const counts = new Map();
    participants.races.forEach((race) => counts.set(race.m, (counts.get(race.m) ?? 0) + 1));
    return participants.modes.reduce((best, mode, index) => (
      (counts.get(index) ?? 0) > (counts.get(best) ?? 0) ? index : best
    ), 0);
  }

  function buildToggle(container, options, activeId, onSelect) {
    container.replaceChildren(
      ...options.map(({ id, label }) => {
        const button = element("button", "mode-toggle__button", label);
        button.type = "button";
        button.setAttribute("aria-pressed", String(id === activeId));
        button.addEventListener("click", () => onSelect(id));
        return button;
      }),
    );
  }

  function raceLink(raceIndex, text) {
    const link = element("a", "records-muted-link", text);
    link.href = RACE_ROOT + participants.races[raceIndex].r;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }

  function racerCell(playerIndex, size) {
    const player = participants.players[playerIndex];
    const cell = element("span", "records-racer");
    cell.appendChild(UI.avatar(playerIndex, size));
    const link = element("a", "racer-link", player.name);
    link.href = playerHash(player.id, ALL_MODES);
    cell.appendChild(link);
    return cell;
  }

  function modeDot(modeIndex) {
    const modeName = participants.modes[modeIndex];
    const dot = element("span", "records-mode-dot");
    dot.style.background = modeColor(modeName);
    dot.tabIndex = 0;
    dot.setAttribute("aria-label", modeLabel(modeName));
    bindTooltip(dot, modeLabel(modeName));
    return dot;
  }

  // One list row: rank, racer, optional muted suffix, value, optional mode dot.
  function listRow(rank, playerIndex, { suffix = null, value, tip = null, modeIndex = null }) {
    const row = element("li", "records-row");
    row.appendChild(element("span", "records-row__rank", String(rank)));
    const who = racerCell(playerIndex, 22);
    if (suffix) {
      who.append(" ", suffix);
    }
    row.appendChild(who);
    const valueNode = element("span", "records-row__value");
    valueNode.append(value);
    if (tip) {
      valueNode.tabIndex = 0;
      bindTooltip(valueNode, tip);
    }
    row.appendChild(valueNode);
    row.appendChild(modeIndex === null ? element("span", "records-mode-dot records-mode-dot--none") : modeDot(modeIndex));
    return row;
  }

  function emptyRow(text) {
    return element("li", "records-row records-row--empty", text);
  }

  function rankedList(rows) {
    const list = element("ol", "records-list");
    list.append(...(rows.length ? rows : [emptyRow("None yet")]));
    return list;
  }

  function card(title, className = "") {
    const node = element("section", `records-card ${className}`.trim());
    node.appendChild(element("h4", "records-card__label", title));
    return node;
  }

  function photoCard() {
    const rows = participants.records.photo.slice(0, CARD_ROWS).map(([raceIndex, winner, runnerUp, margin], index) => {
      const over = raceLink(raceIndex, `over ${participants.players[runnerUp].name}`);
      return listRow(index + 1, winner, {
        suffix: over,
        value: `${margin.toFixed(2)}s`,
        modeIndex: participants.races[raceIndex].m,
      });
    });
    const node = card("Photo finishes");
    node.appendChild(rankedList(rows));
    return node;
  }

  function blowoutCard() {
    const rows = participants.records.blowouts.slice(0, CARD_ROWS).map(([raceIndex, winner, runnerUp, margin], index) => {
      const over = raceLink(raceIndex, `over ${participants.players[runnerUp].name}`);
      return listRow(index + 1, winner, {
        suffix: over,
        value: formatDuration(margin),
        modeIndex: participants.races[raceIndex].m,
      });
    });
    const node = card("Biggest wins");
    node.appendChild(rankedList(rows));
    return node;
  }

  function bigFieldCard() {
    const rows = participants.records.big_field.slice(0, CARD_ROWS).map(([raceIndex, winner, size], index) => {
      const date = raceLink(raceIndex, formatRaceDate(participants.races[raceIndex].d));
      return listRow(index + 1, winner, {
        suffix: date,
        value: plural(size, ["racer", "racers"]),
        modeIndex: participants.races[raceIndex].m,
      });
    });
    const node = card("Biggest field won");
    node.appendChild(rankedList(rows));
    return node;
  }

  function mostRacesCard() {
    const rows = participants.records.most_races.slice(0, CARD_ROWS).map(([player, count], index) => (
      listRow(index + 1, player, { value: plural(count, ["race", "races"]) })
    ));
    const node = card("Most races");
    node.appendChild(rankedList(rows));
    return node;
  }

  function streakBounds(kind, start, end) {
    return kind === "daily"
      ? [start, end]
      : [participants.races[start].d, participants.races[end].d];
  }

  function isStreakActive(kind, end) {
    const endDate = kind === "daily" ? end : participants.races[end].d;
    const earliest = kind === "daily" ? shiftIsoDate(todayInZone(), -1) : daysAgoCutoff(RECENT_DAYS);
    return endDate >= earliest;
  }

  // The exported "current" board keeps only the longest runs, which have mostly ended,
  // so active streaks come from every racer's own current run instead.
  function activeStreaks(kind) {
    const entries = [];
    participants.players.forEach((player, index) => {
      const current = player.streaks[kind].current;
      if (current && current[0] >= MIN_ACTIVE_STREAK && isStreakActive(kind, current[2])) {
        entries.push([index, ...current]);
      }
    });
    return entries.sort((a, b) => b[1] - a[1] || (a[3] < b[3] ? 1 : a[3] > b[3] ? -1 : 0) || a[0] - b[0]);
  }

  function streakList({ kind, label, unit }, view) {
    const entries = view === "current" ? activeStreaks(kind) : participants.records.streaks[kind].best;
    const rows = entries.slice(0, CARD_ROWS).map(([player, length, start, end], index) => {
      const [from, to] = streakBounds(kind, start, end);
      const span = from === to ? formatRaceDate(from) : `${formatRaceDate(from)} – ${formatRaceDate(to)}`;
      return listRow(index + 1, player, { value: plural(length, unit), tip: span });
    });
    const group = element("div", "records-streak");
    group.appendChild(element("h5", "records-card__sublabel", label));
    group.appendChild(rankedList(view === "current" && !rows.length ? [emptyRow("None active")] : rows));
    return group;
  }

  function streaksCard() {
    const node = card("Streaks", "records-card--wide");
    const toggle = element("div", "mode-toggle records-card__toggle");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "Streak board");
    const lists = element("div", "records-streaks");

    function draw() {
      buildToggle(toggle, STREAK_VIEWS, state.streakView, (view) => {
        state.streakView = view;
        draw();
      });
      lists.replaceChildren(...STREAK_KINDS.map((spec) => streakList(spec, state.streakView)));
    }

    node.querySelector(".records-card__label").after(toggle);
    node.appendChild(lists);
    draw();
    return node;
  }

  function sectionHeading(text) {
    const heading = element("div", "heatmap-heading records-heading");
    heading.appendChild(element("h3", null, text));
    return heading;
  }

  function summaryTile(label, content) {
    const item = element("div", "summary-item");
    item.appendChild(element("span", "summary-label", label));
    const strong = element("strong");
    strong.append(content);
    item.appendChild(strong);
    return item;
  }

  function currentSteps() {
    const entry = participants.records.modes.find((item) => item.m === state.mode);
    return entry ? entry.steps : [];
  }

  function renderSummary() {
    const steps = currentSteps();
    if (!steps.length) {
      parts.summary.replaceChildren(summaryTile("Current record", "--"));
      return;
    }
    const [raceIndex, holder, seconds] = steps.at(-1);
    const race = participants.races[raceIndex];
    const holderNode = element("span", "records-holder");
    holderNode.appendChild(UI.avatar(holder, 28));
    const link = element("a", null, participants.players[holder].name);
    link.href = playerHash(participants.players[holder].id, ALL_MODES);
    holderNode.appendChild(link);
    const setLink = element("a", null, formatRaceDate(race.d));
    setLink.href = RACE_ROOT + race.r;
    setLink.target = "_blank";
    setLink.rel = "noopener noreferrer";
    const tiles = [
      summaryTile("Current record", UI.time(seconds)),
      summaryTile("Holder", holderNode),
      summaryTile("Set", setLink),
      summaryTile("Times broken", String(steps.length - 1)),
    ];
    bindTooltip(tiles[3], "Record breaks since the first recorded time");
    parts.summary.replaceChildren(...tiles);
  }

  function progressChart(steps, width) {
    const races = participants.races;
    const modeName = participants.modes[state.mode];
    const days = steps.map(([raceIndex]) => dayNumber(races[raceIndex].d));
    const lastDay = Math.max(dayNumber(races.at(-1).d), days.at(-1));
    const pad = Math.max(1, (lastDay - days[0]) * 0.03);
    const points = steps.map(([, , seconds], index) => ({ seconds, day: days[index] }));
    const { svg, x, y } = finishChartFrame({
      id: CHART_ID,
      className: "chart-svg records-chart",
      title: `${modeLabel(modeName)} record progression`,
      description: `Each step is a new record time, ${steps.length - 1} breaks in all.`,
      width,
      points,
      xDomain: [days[0] - pad, lastDay + pad],
    });
    svg.style.setProperty("--record-color", modeColor(modeName));

    let path = `M${x(days[0])},${y(steps[0][2])}`;
    steps.slice(1).forEach(([, , seconds], index) => {
      path += ` H${x(days[index + 1])} V${y(seconds)}`;
    });
    path += ` H${x(lastDay)}`;
    svg.appendChild(svgElement("path", { class: "records-chart__line", d: path }));

    steps.forEach(([raceIndex, holder, seconds], index) => {
      const dot = svgElement("circle", {
        class: "records-chart__dot",
        cx: x(days[index]),
        cy: y(seconds),
        r: DOT_RADIUS,
        tabindex: 0,
      });
      const label = `${formatDuration(seconds)} · ${participants.players[holder].name} · ${formatRaceDate(races[raceIndex].d)}`;
      dot.setAttribute("aria-label", label);
      bindTooltip(dot, label);
      svg.appendChild(dot);
    });
    return svg;
  }

  function renderChart() {
    const steps = currentSteps();
    if (!steps.length) {
      parts.chart.replaceChildren(element("p", "table-empty", "No finished races in this mode."));
      return;
    }
    mountChart(parts.chart, (width) => progressChart(steps, width));
  }

  function presetColumns() {
    return [
      {
        key: "preset",
        label: "Preset",
        align: "left",
        defaultDirection: "asc",
        sortValue: (row) => row.preset.n,
        render: (cell, row) => {
          cell.appendChild(element("span", "records-preset-name", row.preset.n));
          if (row.index === participants.hardest_preset) {
            cell.append(" ", UI.tag("hardest", "Hardest", `Highest DNF rate among presets with ${participants.preset_min_races}+ races`));
          }
        },
      },
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
          const [player, seconds] = row.preset.best;
          cell.append(UI.time(seconds), " ");
          const link = element("a", "racer-link", participants.players[player].name);
          link.href = playerHash(participants.players[player].id, ALL_MODES);
          cell.appendChild(link);
        },
      },
    ];
  }

  function renderPresets() {
    const columns = presetColumns();
    const rows = participants.presets
      .map((preset, index) => ({ preset, index, rank: index }))
      .filter((row) => row.preset.m === state.mode);
    if (!rows.length) {
      parts.presets.replaceChildren(element("p", "table-empty", "No presets in this mode."));
      return;
    }
    renderSortableTable(parts.presets, columns, sortRows(rows, columns, state.presetSort), { sort: state.presetSort }, (column) => {
      state.presetSort = nextSort({ sort: state.presetSort }, column);
      renderPresets();
    });
  }

  function renderModeScoped() {
    buildToggle(
      parts.toggle,
      participants.modes.map((mode, index) => ({ id: index, label: modeLabel(mode) })),
      state.mode,
      (index) => {
        state.mode = index;
        renderModeScoped();
      },
    );
    renderSummary();
    renderChart();
    renderPresets();
  }

  function init() {
    const section = document.querySelector("#records");
    const body = document.querySelector("#records-body");
    if (!section || !body) {
      return;
    }
    state.mode = mostRacedMode();

    parts.toggle = element("div", "mode-toggle");
    parts.toggle.setAttribute("role", "group");
    parts.toggle.setAttribute("aria-label", "Choose a race mode");
    const toolbar = element("div", "records-toolbar");
    toolbar.appendChild(parts.toggle);

    parts.summary = element("div", "summary-strip records-summary");
    parts.chart = element("div", "chart-host records-chart-host");
    parts.presets = element("div", "table-wrap table-wrap--leaderboard records-presets");

    const cards = element("div", "records-grid");
    cards.append(photoCard(), blowoutCard(), bigFieldCard(), mostRacesCard(), streaksCard());

    body.replaceChildren(
      toolbar,
      parts.summary,
      sectionHeading("Record progression"),
      parts.chart,
      sectionHeading("Across all modes"),
      cards,
      sectionHeading("Presets"),
      parts.presets,
    );
    section.hidden = false;
    renderModeScoped();
  }

  return { init };
})();
