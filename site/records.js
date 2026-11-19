const Records = (() => {
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
  // Room below the current record, as a share of the preset's total improvement, so the latest run never sits on the axis.
  const AXIS_FLOOR_ROOM = 0.1;
  // Room either side of a preset's dates, as a share of the days it was raced.
  const AXIS_DATE_ROOM = 0.03;

  const state = {
    mode: null,
    presetSort: { key: "races", direction: "desc" },
  };
  const parts = {};
  // Each mode's progression panels, built the first time the mode is picked and reused after that.
  const progressPanels = new Map();
  let revealObserver = null;
  let stepsByPreset = null;
  let spansByPreset = null;

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

  function card(title, className = "") {
    const node = element("section", `records-card ${className}`.trim());
    node.appendChild(element("h5", "records-card__label", title));
    return node;
  }

  // The Hall of Records is a section of the Pool Room, under its h3; its own parts are h4s.
  function sectionHeading(text, note = null) {
    const heading = element("div", "heatmap-heading records-heading");
    heading.appendChild(element("h3", null, text));
    if (note) {
      heading.appendChild(element("p", null, note));
    }
    return heading;
  }

  function subheading(text) {
    const heading = element("div", "heatmap-heading records-subheading");
    heading.appendChild(element("h4", null, text));
    return heading;
  }

  // The mode's presets in the violins' ranking: most races first, then by name.
  function presetsInMode(modeIndex) {
    const presets = participants.presets;
    return presets
      .map((_, index) => index)
      .filter((index) => presets[index].m === modeIndex)
      .sort((left, right) => presets[right].races - presets[left].races || presets[left].n.localeCompare(presets[right].n));
  }

  function isCharted(presetIndex) {
    return participants.presets[presetIndex].races >= participants.preset_min_races;
  }

  function presetSteps(presetIndex) {
    stepsByPreset ??= new Map(participants.records.presets.map(({ p, steps }) => [p, steps]));
    return stepsByPreset.get(presetIndex) ?? [];
  }

  // The day numbers of a preset's first and last race.
  function presetSpan(presetIndex) {
    if (!spansByPreset) {
      spansByPreset = new Map();
      participants.races.forEach((race) => {
        if (race.p === null) {
          return;
        }
        const day = dayNumber(race.d);
        const span = spansByPreset.get(race.p);
        spansByPreset.set(race.p, span ? { first: span.first, last: day } : { first: day, last: day });
      });
    }
    return spansByPreset.get(presetIndex);
  }

  // Mode rail

  function renderRail() {
    parts.rail.replaceChildren(
      ...participants.modes.map((modeName, modeIndex) => {
        const count = presetsInMode(modeIndex).length;
        const button = element("button", "mode-toggle__button records-rail__button");
        button.type = "button";
        button.setAttribute("aria-pressed", String(modeIndex === state.mode));
        button.setAttribute("aria-label", `${modeLabel(modeName)}, ${plural(count, ["preset", "presets"])}`);
        button.append(
          UI.modeDot(modeName),
          element("span", "records-rail__label", modeLabel(modeName)),
          element("span", "records-rail__count", String(count)),
        );
        button.addEventListener("click", () => {
          state.mode = modeIndex;
          renderModeScoped();
        });
        return button;
      }),
    );
  }

  // Current records

  function boardCell(content, className = null) {
    const cell = element("td", className);
    cell.append(content);
    return cell;
  }

  function boardRow(presetIndex) {
    const preset = participants.presets[presetIndex];
    const steps = presetSteps(presetIndex);
    const row = element("tr");
    const name = element("span", "records-board__preset", preset.n);
    if (!isCharted(presetIndex)) {
      row.classList.add("records-board__row--few");
      name.tabIndex = 0;
      bindTooltip(name, `${plural(preset.races, ["race", "races"])}: too few to chart (${participants.preset_min_races}+)`);
    }
    row.appendChild(boardCell(name));
    if (!steps.length) {
      const empty = boardCell("No timed finish yet", "align-left records-board__empty");
      empty.colSpan = 4;
      row.appendChild(empty);
      return row;
    }
    const [raceIndex, holder, seconds] = steps.at(-1);
    // The global marked-row rule for tables (styles.css) does the rest.
    UI.markRacers(row, [holder]);
    const breaks = steps.length - 1;
    const broken = element("span", "records-board__breaks", `×${breaks}`);
    broken.tabIndex = 0;
    bindTooltip(broken, breaks
      ? `Broken ${plural(breaks, ["time", "times"])} since the first recorded time`
      : "Not broken since the first recorded time");
    row.append(
      boardCell(UI.time(seconds), "records-board__time"),
      boardCell(racerCell(holder, 22), "align-left"),
      boardCell(raceLink(raceIndex, formatRaceDate(participants.races[raceIndex].d)), "align-left"),
      boardCell(broken),
    );
    return row;
  }

  function renderBoard(presetIndexes) {
    parts.boardTitle.textContent = `Current records · ${modeLabel(participants.modes[state.mode])}`;
    if (!presetIndexes.length) {
      parts.board.replaceChildren(element("p", "table-empty", "No presets in this mode."));
      return;
    }
    const headRow = element("tr");
    [["Preset", null], ["Record", null], ["Holder", "align-left"], ["Set", "align-left"], ["Broken", null]].forEach(([label, className]) => {
      const th = element("th", className, label);
      th.scope = "col";
      headRow.appendChild(th);
    });
    const head = element("thead");
    head.appendChild(headRow);
    const body = element("tbody");
    body.append(...presetIndexes.map(boardRow));
    const table = element("table", "records-board");
    table.append(head, body);
    parts.board.replaceChildren(table);
  }

  // Record progression

  // Each preset's own dates: many ran for only a season or two, and the archive's full span would squash them.
  function presetTimeline(presetIndex) {
    const { first, last } = presetSpan(presetIndex);
    const pad = Math.max(1, (last - first) * AXIS_DATE_ROOM);
    return { domain: [first - pad, last + pad], lastDay: last };
  }

  function stepLabel(steps, index) {
    const [raceIndex, holder, seconds] = steps[index];
    const change = index === 0 ? "first recorded time" : `${formatDuration(steps[index - 1][2] - seconds)} faster`;
    return `${formatDuration(seconds)} · ${participants.players[holder].name} · ${formatRaceDate(participants.races[raceIndex].d)} · ${change}`;
  }

  // A staircase where each record holds until the next one drops it, ending at the preset's last race.
  function progressChart(presetIndex, steps, width) {
    const races = participants.races;
    const preset = participants.presets[presetIndex];
    const { domain, lastDay } = presetTimeline(presetIndex);
    const id = `${CHART_ID}-${presetIndex}`;
    const first = steps[0];
    const current = steps.at(-1);
    const days = steps.map(([raceIndex]) => dayNumber(races[raceIndex].d));
    // The frame fits its time axis to these points; the extra one only sets the floor.
    const floor = { seconds: current[2] - (first[2] - current[2]) * AXIS_FLOOR_ROOM, day: lastDay };
    const { svg, defs, x, y, plotBottom, clipPath } = finishChartFrame({
      id,
      className: "chart-svg step-chart",
      title: `${preset.n} record progression`,
      description: `${plural(steps.length, ["record", "records"])}, from ${formatDuration(first[2])} on ${formatRaceDate(races[first[0]].d)} to ${formatDuration(current[2])} on ${formatRaceDate(races[current[0]].d)}.`,
      width,
      points: [...steps.map(([, , seconds], index) => ({ seconds, day: days[index] })), floor],
      xDomain: domain,
      layout: CHART_LAYOUT,
    });
    svg.style.setProperty("--step-color", modeColor(participants.modes[preset.m]));

    const { wash, line, at } = stepLine(defs, {
      id,
      points: steps.map(([, , seconds], index) => [x(days[index]), y(seconds)]),
      endX: x(lastDay),
      plotBottom,
      clipPath,
    });
    svg.append(wash, line);
    steps.forEach(([, holder, seconds], index) => {
      const kind = index === steps.length - 1 ? "current" : index === 0 ? "first" : "break";
      svg.appendChild(stepMark({ cx: x(days[index]), cy: y(seconds), kind, label: stepLabel(steps, index), at: at[index], holder }));
    });
    return svg;
  }

  // The panel's header is the chart's end label: the preset and its current record.
  function progressPanel(presetIndex) {
    const preset = participants.presets[presetIndex];
    const steps = presetSteps(presetIndex);
    const node = card(preset.n, "records-progress");
    const label = node.querySelector(".records-card__label");
    label.prepend(UI.modeDot(participants.modes[preset.m]));
    const host = element("div", "chart-host records-chart-host step-chart-host step-chart-host--pending");
    if (steps.length) {
      const record = element("span", "records-progress__record");
      record.appendChild(UI.time(steps.at(-1)[2]));
      label.after(record);
    }
    node.appendChild(host);
    if (steps.length) {
      mountChart(host, (width) => progressChart(presetIndex, steps, width), CHART_MIN_WIDTH);
    } else {
      host.appendChild(element("p", "table-empty", "No timed finish yet."));
    }
    return node;
  }

  // Each staircase draws itself once, the first time its panel scrolls into view. Panels that
  // arrive together stagger by --i; a panel shown again later keeps its finished drawing.
  function reveal(panels) {
    revealObserver ??= new IntersectionObserver((entries) => {
      const shown = entries.filter((entry) => entry.isIntersecting).map((entry) => entry.target);
      shown.forEach((panel, order) => {
        revealObserver.unobserve(panel);
        panel.style.setProperty("--i", order);
      });
      requestAnimationFrame(() => shown.forEach((panel) => {
        panel.querySelector(".step-chart-host").classList.remove("step-chart-host--pending");
      }));
    }, { threshold: 0.4 });
    panels.forEach((panel) => revealObserver.observe(panel));
  }

  function renderProgress(presetIndexes) {
    const charted = presetIndexes.filter(isCharted);
    if (!progressPanels.has(state.mode)) {
      const panels = charted.map(progressPanel);
      progressPanels.set(state.mode, panels);
      parts.progress.replaceChildren(...panels);
      reveal(panels);
    } else {
      parts.progress.replaceChildren(...progressPanels.get(state.mode));
      // A cached panel may have been off the page when the viewer's racer changed.
      UI.syncMarks(parts.progress);
    }
    parts.progress.hidden = !charted.length;
    const left = presetIndexes.filter((index) => !isCharted(index)).map((index) => participants.presets[index].n);
    parts.progressNote.hidden = !left.length;
    parts.progressNote.textContent = charted.length
      ? `Not charted, with fewer than ${participants.preset_min_races} races: ${left.join(", ")}.`
      : `No preset in this mode has ${participants.preset_min_races}+ races to chart.`;
  }

  // Finish times

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
    // The viewer's own key, shown in place while a racer is set: the highlighted dots' outline, as on a racer page.
    parts.youKey = element("span");
    parts.youKey.append(element("span", "racer-key"), "You ");
    syncYouKey();
    legend.append(parts.youKey, element("span", "line-key"), "Median ", element("span", "line-key line-key--gold"), "Gold median");
    return legend;
  }

  function syncYouKey() {
    parts.youKey.hidden = meIndex() === null;
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
      // The viewer's dots are drawn on top; the field stays at full strength (fadeOthers false).
      highlightPlayer: meIndex(),
      fadeOthers: false,
      title: `${modeName} finish times by preset`,
      description: "One violin per preset: each dot is one finish, the shape shows where finishes cluster, the bold line marks the median, and the gold line marks the median winning time.",
      emptyText: `No preset in this mode has ${VIOLIN_MIN_PRESET_RACES}+ races.`,
    });
  }

  // Closed until opened; a chart in it draws once its host has a width (see mountChart).
  function finishTimesDetails() {
    const details = element("details", "data-details records-finishes");
    details.appendChild(element("summary", null, "Finish times by preset"));
    details.append(violinLegend(), parts.violinNote, parts.violin, parts.presets);
    return details;
  }

  function renderModeScoped() {
    const presetIndexes = presetsInMode(state.mode);
    renderRail();
    renderBoard(presetIndexes);
    renderProgress(presetIndexes);
    renderFinishTimes();
    renderPresets();
  }

  // Fills its body inside the Pool Room panel; PoolRoom shows the section.
  function init() {
    const body = document.querySelector("#records-body");
    if (!body) {
      return;
    }
    state.mode = mostRacedMode();

    parts.rail = element("div", "mode-toggle records-rail");
    parts.rail.setAttribute("role", "group");
    parts.rail.setAttribute("aria-label", "Choose a race mode");

    const boardHeading = subheading("");
    parts.boardTitle = boardHeading.querySelector("h4");
    parts.board = element("div", "table-wrap table-wrap--leaderboard records-board-wrap");
    parts.progress = element("div", "records-grid");
    parts.progressNote = element("p", "chart-note records-progress-note");
    parts.violinNote = element("p", "chart-note");
    parts.violin = element("div", "chart-host chart-host--violin");
    parts.violin.setAttribute("aria-live", "polite");
    parts.presets = element("div", "table-wrap table-wrap--leaderboard presets-table");

    const main = element("div", "records-main");
    main.append(
      boardHeading,
      parts.board,
      subheading("Record progression"),
      parts.progress,
      parts.progressNote,
      finishTimesDetails(),
    );
    const layout = element("div", "records-layout");
    layout.append(parts.rail, main);
    body.replaceChildren(sectionHeading("Hall of Records", "Fastest time on every preset"), layout);
    renderModeScoped();
    // The marks follow by UI.markRacers; the violins and their key change with the viewer's racer, in place.
    document.addEventListener(ME_EVENT, () => {
      syncYouKey();
      renderFinishTimes();
    });
  }

  return { init };
})();
