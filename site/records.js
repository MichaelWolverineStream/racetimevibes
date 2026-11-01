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
  // The height must match .records-chart-host in records.css.
  const CHART_LAYOUT = {
    height: 220,
    margin: { top: 14, right: 14, bottom: 24, left: 40 },
    axisTitles: false,
    maxTimeIntervals: 4,
  };
  // Narrow enough that two columns never scroll sideways, down to the 620px breakpoint.
  const CHART_MIN_WIDTH = 220;
  // Room below the current record, as a share of the mode's total improvement, so the latest run never sits on the axis.
  const AXIS_FLOOR_ROOM = 0.1;

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
    const dot = UI.modeDot(modeName);
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
    row.appendChild(modeIndex === null ? element("span", "ui-mode-dot ui-mode-dot--none") : modeDot(modeIndex));
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
    const node = card(`Biggest wins · ${participants.blowout_min_field}+ racers`);
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

  function rivalsCard() {
    const rows = participants.records.rivals.slice(0, CARD_ROWS).map(([first, second, count], index) => {
      const partner = participants.players[second];
      const link = element("a", "records-muted-link", `& ${partner.name}`);
      link.href = playerHash(partner.id, ALL_MODES);
      return listRow(index + 1, first, { suffix: link, value: plural(count, ["race", "races"]) });
    });
    const node = card("Most-met rivals");
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
    const node = card("Streaks", "records-card--wide records-card--tall");
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

  function sectionHeading(text, note = null) {
    const heading = element("div", "heatmap-heading records-heading");
    heading.appendChild(element("h3", null, text));
    if (note) {
      heading.appendChild(element("p", null, note));
    }
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

  function modeSteps(modeIndex) {
    const entry = participants.records.modes.find((item) => item.m === modeIndex);
    return entry ? entry.steps : [];
  }

  function renderSummary() {
    const steps = modeSteps(state.mode);
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

  // Every panel shares one date axis, from the first race in the archive to the latest.
  function progressTimeline() {
    const firstDay = dayNumber(participants.races[0].d);
    const lastDay = dayNumber(participants.races.at(-1).d);
    const pad = Math.max(1, (lastDay - firstDay) * 0.03);
    return { domain: [firstDay - pad, lastDay + pad], lastDay };
  }

  function stepLabel(steps, index) {
    const [raceIndex, holder, seconds] = steps[index];
    const change = index === 0 ? "first recorded time" : `${formatDuration(steps[index - 1][2] - seconds)} faster`;
    return `${formatDuration(seconds)} · ${participants.players[holder].name} · ${formatRaceDate(participants.races[raceIndex].d)} · ${change}`;
  }

  // A staircase where each record holds until the next one drops it.
  function progressChart(modeIndex, steps, { domain, lastDay }, width) {
    const races = participants.races;
    const modeName = participants.modes[modeIndex];
    const id = `${CHART_ID}-${modeName}`;
    const first = steps[0];
    const current = steps.at(-1);
    const days = steps.map(([raceIndex]) => dayNumber(races[raceIndex].d));
    // The frame fits its time axis to these points; the extra one only sets the floor.
    const floor = { seconds: current[2] - (first[2] - current[2]) * AXIS_FLOOR_ROOM, day: lastDay };
    const { svg, defs, x, y, plotBottom, clipPath } = finishChartFrame({
      id,
      className: "chart-svg step-chart",
      title: `${modeLabel(modeName)} record progression`,
      description: `${plural(steps.length, ["record", "records"])}, from ${formatDuration(first[2])} on ${formatRaceDate(races[first[0]].d)} to ${formatDuration(current[2])} on ${formatRaceDate(races[current[0]].d)}.`,
      width,
      points: [...steps.map(([, , seconds], index) => ({ seconds, day: days[index] })), floor],
      xDomain: domain,
      layout: CHART_LAYOUT,
    });
    svg.style.setProperty("--step-color", modeColor(modeName));

    const { wash, line, at } = stepLine(defs, {
      id,
      points: steps.map(([, , seconds], index) => [x(days[index]), y(seconds)]),
      endX: x(lastDay),
      plotBottom,
      clipPath,
    });
    svg.append(wash, line);
    steps.forEach(([, , seconds], index) => {
      const kind = index === steps.length - 1 ? "current" : index === 0 ? "first" : "break";
      svg.appendChild(stepMark({ cx: x(days[index]), cy: y(seconds), kind, label: stepLabel(steps, index), at: at[index] }));
    });
    return svg;
  }

  // The panel's header is the chart's end label: the mode and its current record.
  function progressPanel(modeIndex, steps, host) {
    const modeName = participants.modes[modeIndex];
    const node = card(modeLabel(modeName), "records-progress");
    const label = node.querySelector(".records-card__label");
    label.prepend(UI.modeDot(modeName));
    if (steps.length) {
      const record = element("span", "records-progress__record");
      record.appendChild(UI.time(steps.at(-1)[2]));
      label.after(record);
    }
    node.appendChild(host);
    return node;
  }

  // Each staircase draws itself once, the first time its panel scrolls into view. Panels that
  // arrive together stagger by --i; a redraw after that inserts finished nodes, so nothing replays.
  function revealOnView(panels) {
    const observer = new IntersectionObserver((entries) => {
      const shown = entries.filter((entry) => entry.isIntersecting).map((entry) => entry.target);
      shown.forEach((panel, order) => {
        observer.unobserve(panel);
        panel.style.setProperty("--i", order);
      });
      requestAnimationFrame(() => shown.forEach((panel) => {
        panel.querySelector(".step-chart-host").classList.remove("step-chart-host--pending");
      }));
    }, { threshold: 0.4 });
    panels.forEach((panel) => observer.observe(panel));
  }

  function renderProgress() {
    const timeline = progressTimeline();
    const panels = participants.modes.map((modeName, modeIndex) => {
      const steps = modeSteps(modeIndex);
      const host = element("div", "chart-host records-chart-host step-chart-host step-chart-host--pending");
      const panel = progressPanel(modeIndex, steps, host);
      parts.progress.appendChild(panel);
      if (steps.length) {
        mountChart(host, (width) => progressChart(modeIndex, steps, timeline, width), CHART_MIN_WIDTH);
      } else {
        host.appendChild(element("p", "table-empty", "No finished races in this mode."));
      }
      return panel;
    });
    revealOnView(panels);
  }

  function fastestRacerLink([player]) {
    const link = element("a", "racer-link", participants.players[player].name);
    link.href = playerHash(participants.players[player].id, ALL_MODES);
    return link;
  }

  function renderPresets() {
    const columns = presetColumns({ fastestLink: fastestRacerLink });
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

  function violinLegend() {
    const legend = element("p", "violin-legend violin-legend--standalone");
    legend.append(element("span", "line-key"), "Median ", element("span", "line-key line-key--gold"), "Gold median");
    return legend;
  }

  // The mode's most-raced presets, one violin each; the presets table below lists them all.
  function renderFinishTimes() {
    const presets = participants.presets;
    const inMode = presets.map((_, index) => index).filter((index) => presets[index].m === state.mode);
    const charted = chartedPresets(inMode, (left, right) =>
      presets[right].races - presets[left].races || presets[left].n.localeCompare(presets[right].n),
    );
    parts.violinNote.hidden = charted.length === inMode.length;
    parts.violinNote.textContent = `Charting ${charted.length} of this mode's ${inMode.length} presets: the most raced among those with ${VIOLIN_MIN_PRESET_RACES}+ races, up to ${VIOLIN_SLOTS}. The table lists them all.`;
    const modeName = modeLabel(participants.modes[state.mode]);
    renderPresetViolins(parts.violin, charted, {
      id: "records-violin",
      highlightPlayer: null,
      title: `${modeName} finish times by preset`,
      description: "One violin per preset: each dot is one finish, the shape shows where finishes cluster, the bold line marks the median, and the gold line marks the median winning time.",
      emptyText: `No preset in this mode has ${VIOLIN_MIN_PRESET_RACES}+ races.`,
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
    renderFinishTimes();
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
    parts.violinNote = element("p", "chart-note");
    parts.violin = element("div", "chart-host chart-host--violin");
    parts.violin.setAttribute("aria-live", "polite");
    parts.progress = element("div", "records-grid");
    parts.presets = element("div", "table-wrap table-wrap--leaderboard presets-table");

    const cards = element("div", "records-grid");
    cards.append(photoCard(), blowoutCard(), bigFieldCard(), mostRacesCard(), streaksCard(), rivalsCard());

    body.replaceChildren(
      sectionHeading("Across all modes"),
      cards,
      sectionHeading("Record progression", "Record time by race date"),
      parts.progress,
      sectionHeading("By mode"),
      toolbar,
      parts.summary,
      violinLegend(),
      parts.violinNote,
      parts.violin,
      parts.presets,
    );
    section.hidden = false;
    renderProgress();
    renderModeScoped();
  }

  return { init };
})();
