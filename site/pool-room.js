const PoolRoom = (() => {
  // Must match HALL_ROWS in feed.js: the featured racer's Hall of Fame honour is a spot in a card's top 5.
  const CARD_ROWS = 5;
  // A run shorter than this is never marked as still going.
  const MIN_LIVE_STREAK = 2;
  const ALL_TIME = "all";
  const STREAK_KINDS = [
    { kind: "daily", label: "Days in a row", unit: ["day", "days"] },
    { kind: "win", label: "Wins in a row", unit: ["win", "wins"] },
    { kind: "podium", label: "Podiums in a row", unit: ["podium", "podiums"] },
    { kind: "finish", label: "Finishes in a row", unit: ["finish", "finishes"] },
  ];
  const PODIUM_FINAL = { 1: "Champion", 2: "Runner-up", 3: "Third" };
  const PODIUM_SO_FAR = { 1: "1st so far", 2: "2nd so far", 3: "3rd so far" };

  // `season` is a season number, or null for all time.
  const state = { season: null };
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

  function raceLink(raceIndex, text) {
    const link = element("a", "pool-muted-link", text);
    link.href = RACE_ROOT + participants.races[raceIndex].r;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  }

  function racerCell(playerIndex, size) {
    const player = participants.players[playerIndex];
    const cell = element("span", "pool-racer");
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

  // Sits in a streak row's mode-dot column, which a streak row leaves empty.
  function livePip() {
    const pip = element("span", "pool-live");
    pip.tabIndex = 0;
    pip.setAttribute("role", "img");
    pip.setAttribute("aria-label", "Still going");
    bindTooltip(pip, "Still going");
    return pip;
  }

  // One list row: rank, racer, optional muted suffix, value, then a mode dot, a live pip or nothing.
  function listRow(rank, playerIndex, { suffix = null, value, tip = null, modeIndex = null, live = false }) {
    const row = element("li", "pool-row");
    row.appendChild(element("span", "pool-row__rank", String(rank)));
    const who = racerCell(playerIndex, 22);
    if (suffix) {
      who.append(" ", suffix);
    }
    row.appendChild(who);
    const valueNode = element("span", "pool-row__value");
    valueNode.append(value);
    if (tip) {
      valueNode.tabIndex = 0;
      bindTooltip(valueNode, tip);
    }
    row.appendChild(valueNode);
    if (modeIndex !== null) {
      row.appendChild(modeDot(modeIndex));
    } else if (live) {
      row.appendChild(livePip());
    } else {
      row.appendChild(element("span", "ui-mode-dot ui-mode-dot--none"));
    }
    return row;
  }

  function rankedList(rows) {
    const list = element("ol", "pool-list");
    list.append(...(rows.length ? rows : [element("li", "pool-row pool-row--empty", "None yet")]));
    return list;
  }

  function card(title, className = "") {
    const node = element("section", `pool-card ${className}`.trim());
    node.appendChild(element("h4", "pool-card__label", title));
    return node;
  }

  function sectionHeading(text) {
    const heading = element("div", "heatmap-heading pool-heading");
    heading.appendChild(element("h3", null, text));
    return heading;
  }

  // Champions

  // Season dates are Eastern dates already, so they format in UTC; an upcoming slot is a moment, so it formats in Eastern Time.
  const seasonDateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const slotDateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/New_York" });

  function seasonDay(isoDate) {
    return new Date(`${isoDate}T00:00:00Z`);
  }

  // Every season with a race, oldest first, then the next season once it has a name but no race yet.
  function timelineSeasons() {
    const seasons = participants.seasons.map((season) => ({ ...season, upcoming: false }));
    const next = seasons.at(-1).n + 1;
    return seasonName(next) ? [...seasons, { n: next, upcoming: true }] : seasons;
  }

  function seasonDates(season) {
    if (season.upcoming) {
      const slot = participants.upcoming.find((row) => row[3] === season.n);
      return slot ? `Starts ${slotDateFormatter.format(new Date(slot[0] * 1000))}` : "Not started";
    }
    if (!season.ended) {
      return `Since ${seasonDateFormatter.format(seasonDay(season.first))}`;
    }
    return seasonDateFormatter.formatRange(seasonDay(season.first), seasonDay(season.last));
  }

  function showSeasonOnLeaderboard(season) {
    selectLeaderboardSeason(season);
    const title = document.getElementById("leaderboard-title");
    title.scrollIntoView({ block: "start" });
    title.focus({ preventScroll: true });
  }

  // A season with a race opens its leaderboard; an upcoming one has none yet, so its title is plain text.
  function seasonTitleNode(season) {
    const name = seasonName(season.n);
    const text = seasonTitle(season.n);
    if (season.upcoming) {
      return [element("span", "pool-season__title", text)];
    }
    const button = element("button", "pool-season__title", text);
    button.type = "button";
    button.addEventListener("click", () => showSeasonOnLeaderboard(season.n));
    if (name?.kind !== "retro") {
      return [button];
    }
    // The short name is the visible label; the full name describes it, for the tooltip and for screen readers.
    const full = element("span", "visually-hidden", `${name.full}, named in retrospect`);
    full.id = `pool-season-${season.n}-full`;
    button.setAttribute("aria-describedby", full.id);
    bindTooltip(button, full.textContent);
    return [button, full];
  }

  // The overall top 3: final once the season has ended, standings so far while it runs.
  function podiumBlock(season) {
    const places = seasonPodium(season.n);
    if (!places.length) {
      return [];
    }
    const roles = season.ended ? PODIUM_FINAL : PODIUM_SO_FAR;
    const block = element("div", "pool-podium");
    block.appendChild(element("span", "pool-season__role", season.ended ? "Podium" : "Leading"));
    const list = element("ol", "pool-podium__list");
    list.append(...places.map(([rank, playerIndex]) => {
      const row = element("li", `pool-podium__row pool-podium__row--${rank}`);
      const pip = element("span", "pool-podium__pip", String(rank));
      pip.setAttribute("aria-hidden", "true");
      bindTooltip(pip, roles[rank]);
      row.append(pip, element("span", "visually-hidden", `${roles[rank]}: `), racerCell(playerIndex, rank === 1 ? 22 : 18));
      return row;
    }));
    block.appendChild(list);
    return [block];
  }

  function namedBy(season) {
    const namers = seasonNamers(season.n);
    if (!namers.length) {
      return [];
    }
    const line = element("div", "pool-season__people");
    line.appendChild(element("span", "pool-season__role", "Named by"));
    line.append(...namers.map((playerIndex) => racerCell(playerIndex, 18)));
    return [line];
  }

  function seasonCard(season) {
    const kind = seasonName(season.n)?.kind ?? "unnamed";
    const node = element("li", `pool-season pool-season--${kind}`);
    if (season.upcoming) {
      node.classList.add("pool-season--upcoming");
    }
    const head = element("div", "pool-season__head");
    head.appendChild(element("span", "pool-season__number", `Season ${season.n}`));
    if (season.upcoming || !season.ended) {
      head.appendChild(element("span", "pool-season__status", season.upcoming ? "Upcoming" : "Current"));
    }
    node.append(head, ...seasonTitleNode(season), element("span", "pool-season__dates", seasonDates(season)));
    if (!season.upcoming) {
      node.append(...podiumBlock(season));
    }
    node.append(...namedBy(season));
    return node;
  }

  function seasonTimeline() {
    const list = element("ol", "pool-seasons");
    list.append(...timelineSeasons().map(seasonCard));
    return list;
  }

  // Awards

  function photoCard(boards) {
    const rows = boards.photo.slice(0, CARD_ROWS).map(([raceIndex, winner, runnerUp, margin], index) => (
      listRow(index + 1, winner, {
        suffix: raceLink(raceIndex, `over ${participants.players[runnerUp].name}`),
        value: `${margin.toFixed(2)}s`,
        modeIndex: participants.races[raceIndex].m,
      })
    ));
    const node = card("Photo finishes");
    node.appendChild(rankedList(rows));
    return node;
  }

  function blowoutCard(boards) {
    const rows = boards.blowouts.slice(0, CARD_ROWS).map(([raceIndex, winner, runnerUp, margin], index) => (
      listRow(index + 1, winner, {
        suffix: raceLink(raceIndex, `over ${participants.players[runnerUp].name}`),
        value: formatDuration(margin),
        modeIndex: participants.races[raceIndex].m,
      })
    ));
    const node = card(`Biggest wins · ${participants.blowout_min_field}+ racers`);
    node.appendChild(rankedList(rows));
    return node;
  }

  function bigFieldCard(boards) {
    const rows = boards.big_field.slice(0, CARD_ROWS).map(([raceIndex, winner, size], index) => (
      listRow(index + 1, winner, {
        suffix: raceLink(raceIndex, formatRaceDate(participants.races[raceIndex].d)),
        value: plural(size, ["racer", "racers"]),
        modeIndex: participants.races[raceIndex].m,
      })
    ));
    const node = card("Biggest field won");
    node.appendChild(rankedList(rows));
    return node;
  }

  function mostRacesCard(boards) {
    const rows = boards.most_races.slice(0, CARD_ROWS).map(([player, count], index) => (
      listRow(index + 1, player, { value: plural(count, ["race", "races"]) })
    ));
    const node = card("Most races");
    node.appendChild(rankedList(rows));
    return node;
  }

  function rivalsCard(boards) {
    const rows = boards.rivals.slice(0, CARD_ROWS).map(([first, second, count], index) => {
      const partner = participants.players[second];
      const link = element("a", "pool-muted-link", `& ${partner.name}`);
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

  // A daily streak is active through yesterday in Eastern Time; any other kind while its last race is recent.
  function isStreakActive(kind, end) {
    const endDate = kind === "daily" ? end : participants.races[end].d;
    const earliest = kind === "daily" ? shiftIsoDate(todayInZone(), -1) : daysAgoCutoff(RECENT_DAYS);
    return endDate >= earliest;
  }

  // A board row is still going when it ends where the racer's current run ends, and that run is active.
  function isLive(kind, playerIndex, length, end) {
    const current = participants.players[playerIndex].streaks[kind].current;
    return length >= MIN_LIVE_STREAK && current?.[2] === end && isStreakActive(kind, end);
  }

  function streakList({ kind, label, unit }, boards, markLive) {
    const rows = boards.streaks[kind].best.slice(0, CARD_ROWS).map(([player, length, start, end], index) => {
      const [from, to] = streakBounds(kind, start, end);
      const span = from === to ? formatRaceDate(from) : `${formatRaceDate(from)} – ${formatRaceDate(to)}`;
      return listRow(index + 1, player, {
        value: plural(length, unit),
        tip: span,
        live: markLive && isLive(kind, player, length, end),
      });
    });
    const group = element("div", "pool-streak");
    group.appendChild(element("h5", "pool-card__sublabel", label));
    group.appendChild(rankedList(rows));
    return group;
  }

  function streaksCard(boards, markLive) {
    const node = card("Streaks", "pool-card--wide pool-card--tall");
    const lists = element("div", "pool-streaks");
    lists.append(...STREAK_KINDS.map((spec) => streakList(spec, boards, markLive)));
    node.appendChild(lists);
    return node;
  }

  // Only a board that reaches today can hold a run that is still going: all time, or a season still running.
  function boardsReachToday(season) {
    return season === null || participants.seasons.some((item) => item.n === season && !item.ended);
  }

  function renderAwards() {
    const season = state.season;
    const boards = season === null ? participants.records : participants.records.seasons[season];
    const markLive = boardsReachToday(season);
    parts.cards.replaceChildren(
      photoCard(boards),
      blowoutCard(boards),
      bigFieldCard(boards),
      mostRacesCard(boards),
      streaksCard(boards, markLive),
      rivalsCard(boards),
    );
  }

  // Every season with a race, newest first, then all time.
  function seasonPicker() {
    const picker = element("div", "pool-picker");
    const label = element("label", "control-label", "Season");
    label.htmlFor = "pool-room-season";
    const select = element("select");
    select.id = "pool-room-season";
    const seasons = participants.seasons.map((season) => season.n).reverse();
    select.append(
      ...seasons.map((season) => new Option(seasonLabel(season), String(season))),
      new Option("All time", ALL_TIME),
    );
    select.value = state.season === null ? ALL_TIME : String(state.season);
    select.addEventListener("change", () => {
      state.season = select.value === ALL_TIME ? null : Number(select.value);
      renderAwards();
    });
    picker.append(label, select);
    return picker;
  }

  function init() {
    const section = document.querySelector("#pool-room");
    const body = document.querySelector("#pool-room-body");
    if (!section || !body) {
      return;
    }
    // With no schedule there are no seasons: no champions, and the awards are all time.
    const hasSeasons = participants.seasons.length > 0;
    state.season = currentSeason();
    parts.cards = element("div", "pool-grid");
    const awardsHeading = sectionHeading("Awards");
    if (hasSeasons) {
      awardsHeading.appendChild(seasonPicker());
    }
    body.replaceChildren(
      ...(hasSeasons ? [sectionHeading("Champions"), seasonTimeline()] : []),
      awardsHeading,
      parts.cards,
    );
    section.hidden = false;
    renderAwards();
  }

  return { init };
})();
