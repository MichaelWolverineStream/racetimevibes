const Feed = (() => {
  const PAGE_SIZE = 20;
  const MAX_VISIBLE_TAGS = 4;
  const MAX_PB_DETAILS = 5;
  const HERO_MIN_LENGTH = 12;
  const HERO_MIN_POOL = 20;
  const HERO_WINDOW_DAYS = 30;
  const HERO_MAX_LENGTH = 180;
  const NEW_BADGE_DAYS = 7;
  const NEW_BADGE_LIMIT = 8;
  const STATUS_DNF = 1;
  const STATUS_DQ = 2;
  const SUMMARY_TAGS = [
    ["pb", "PB"],
    ["record", "Mode record"],
    ["first_win", "First win"],
    ["upset", "Upset"],
    ["photo", "Photo finish"],
    ["milestone", "Milestone"],
    ["badge", "Badge"],
  ];

  const dayFormatter = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });

  let started = false;
  let shownCount = 0;
  let lastRenderedDay = null;
  let firstNewIndex = 0;
  let feedList = null;
  let moreButton = null;
  const raceIndexByName = new Map();

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

  function playerName(playerIndex) {
    return participants.players[playerIndex].name;
  }

  function playerLink(playerIndex, className) {
    const player = participants.players[playerIndex];
    const link = element("a", className, player.name);
    link.href = playerHash(player.id, ALL_MODES);
    return link;
  }

  function lookupPlayerIndex(userId) {
    return typeof playerIndexFor === "function" ? playerIndexFor(userId) : playerIndexById.get(userId);
  }

  function modeName(race) {
    return participants.modes[race.m];
  }

  function placeText(race, entry) {
    const [, place, status] = entry;
    if (status === 0 && place !== null) {
      return `${ordinal(place)} of ${race.n}`;
    }
    return status === STATUS_DQ ? "DQ" : "DNF";
  }

  // 32-bit FNV-1a, so the fact of the day is stable for everyone on the same date.
  function fnv1a(text) {
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619) >>> 0;
    }
    return hash;
  }

  // ---- Since your last visit ----

  function findFirstNewIndex(baseline) {
    const races = participants.races;
    if (baseline.slug) {
      const known = races.findIndex((race) => race.r === baseline.slug);
      if (known >= 0) {
        return known + 1;
      }
    }
    if (baseline.date) {
      const later = races.findIndex((race) => race.d > baseline.date);
      return later >= 0 ? later : races.length;
    }
    return races.length;
  }

  function resolveBaseline() {
    const sessionBaseline = Store.session.get("baseline", null);
    const baseline = sessionBaseline ?? Store.get("lastSeen", null);
    if (sessionBaseline === null) {
      Store.session.set("baseline", baseline ?? {});
    }
    const lastRace = participants.races[participants.races.length - 1];
    Store.set("lastSeen", { slug: lastRace.r, date: lastRace.d });
    return baseline ?? {};
  }

  function renderNewSummary() {
    const host = document.getElementById("updates-new");
    const newCount = participants.races.length - firstNewIndex;
    if (newCount <= 0) {
      return;
    }
    const pill = element("span", "feed-new-pill");
    const dot = element("span", "feed-new-pill__dot", "●");
    dot.setAttribute("aria-hidden", "true");
    pill.append(dot, document.createTextNode(`${newCount} new since your last visit`));
    host.appendChild(pill);

    const counts = new Map();
    participants.races.slice(firstNewIndex).forEach((race) => {
      (race.t || []).forEach((tag) => counts.set(tag[0], (counts.get(tag[0]) ?? 0) + 1));
    });
    SUMMARY_TAGS.forEach(([kind, label]) => {
      const count = counts.get(kind) ?? 0;
      if (count > 0) {
        host.appendChild(UI.tag(kind, count > 1 ? `${label} ×${count}` : label));
      }
    });
  }

  // ---- Feed rows ----

  function badgeDefinition(badgeId) {
    return participants.badges.find((badge) => badge.id === badgeId) ?? { id: badgeId, label: badgeId };
  }

  function pbTag(pbTags) {
    const details = pbTags.slice(0, MAX_PB_DETAILS).map(([, playerIndex, seconds, previous]) => {
      const previousText = previous === null || previous === undefined ? "" : `, previous best ${formatDuration(previous)}`;
      return `${playerName(playerIndex)}: ${formatDuration(seconds)}${previousText}`;
    });
    if (pbTags.length > MAX_PB_DETAILS) {
      details.push(`and ${pbTags.length - MAX_PB_DETAILS} more`);
    }
    return { kind: "pb", label: pbTags.length > 1 ? `PB ×${pbTags.length}` : "PB", detail: details.join("; ") };
  }

  function describeTag(tag) {
    const [kind, playerIndex, a, b] = tag;
    switch (kind) {
      case "record": {
        const previous = b === null || b === undefined ? "" : `, previous record ${formatDuration(b)}`;
        return { kind, label: "Mode record", detail: `${playerName(playerIndex)}: ${formatDuration(a)}${previous}` };
      }
      case "first_win":
        return { kind, label: "First win", detail: `${playerName(playerIndex)} won for the first time` };
      case "photo":
        return {
          kind,
          label: `Photo finish ${Number(b).toFixed(2)}s`,
          detail: `${playerName(playerIndex)} beat ${playerName(a)} by ${Number(b).toFixed(2)}s`,
        };
      case "upset":
        return {
          kind,
          label: "Upset",
          detail: `${playerName(playerIndex)} had a ${Math.round(b)}% win chance against ${playerName(a)}`,
        };
      case "milestone":
        return { kind, label: `${ordinal(a)} race`, detail: `${playerName(playerIndex)} raced for the ${ordinal(a)} time` };
      case "badge": {
        const definition = badgeDefinition(a);
        return { kind, label: definition.label, detail: `${playerName(playerIndex)} unlocked ${definition.label}: ${definition.description ?? ""} · ${UI.badgeShare(a)}` };
      }
      default:
        return null;
    }
  }

  function raceTagDescriptions(race) {
    const tags = race.t || [];
    const pbTags = tags.filter((tag) => tag[0] === "pb");
    const descriptions = [];
    tags.forEach((tag) => {
      if (tag[0] === "pb") {
        if (tag === pbTags[0]) {
          descriptions.push(pbTag(pbTags));
        }
        return;
      }
      const description = describeTag(tag);
      if (description) {
        descriptions.push(description);
      }
    });
    return descriptions;
  }

  function tagCell(race) {
    const cell = element("span", "feed-row__tags");
    const descriptions = raceTagDescriptions(race);
    descriptions.slice(0, MAX_VISIBLE_TAGS).forEach(({ kind, label, detail }) => {
      cell.appendChild(UI.tag(kind, label, detail));
    });
    const hidden = descriptions.slice(MAX_VISIBLE_TAGS);
    if (hidden.length) {
      const more = element("span", "feed-row__more", `+${hidden.length}`);
      more.tabIndex = 0;
      bindTooltip(more, hidden.map((description) => description.label).join(", "));
      cell.appendChild(more);
    }
    return cell;
  }

  function placePill(place) {
    const podium = place >= 1 && place <= 3 ? ` rank-badge--podium rank-badge--${place}` : "";
    return element("span", `rank-badge feed-place${podium}`, String(place));
  }

  function modeCell(race) {
    const name = modeName(race);
    const cell = element("span", "feed-row__mode");
    const dot = element("span", "feed-row__mode-dot");
    dot.style.background = modeColor(name);
    cell.append(dot, element("span", null, name));
    return cell;
  }

  function winnerCell(race) {
    const cell = element("span", "feed-row__winner");
    const winner = race.e.find((entry) => entry[1] === 1 && entry[2] === 0);
    if (!winner) {
      cell.textContent = "—";
      return { cell, winner: null };
    }
    cell.append(placePill(1), UI.avatar(winner[0], 24), playerLink(winner[0], "racer-link"));
    return { cell, winner };
  }

  function ratingDeltaNode(raceIndex, entryIndex) {
    const delta = typeof ratingDelta === "function" ? ratingDelta(raceIndex, entryIndex) : null;
    if (delta === null || delta === undefined) {
      return null;
    }
    const rounded = Math.round(delta);
    if (rounded === 0) {
      return element("span", "trend trend--flat", "0");
    }
    return element("span", rounded > 0 ? "trend trend--up" : "trend trend--down", `${rounded > 0 ? "▲" : "▼"}${Math.abs(rounded)}`);
  }

  function entrantRow(race, raceIndex, entry, entryIndex) {
    const [playerIndex, place, status, seconds] = entry;
    const row = element("li", "feed-entrant");
    const placeCell = element("span", "feed-entrant__place");
    if (status === 0 && place !== null) {
      placeCell.appendChild(placePill(place));
    }
    const nameCell = element("span", "feed-entrant__name");
    nameCell.append(UI.avatar(playerIndex, 20), playerLink(playerIndex, "racer-link"));
    const timeCell = element("span", "feed-entrant__time");
    if (status === 0 && seconds !== null) {
      timeCell.appendChild(UI.time(seconds));
    } else {
      timeCell.textContent = status === STATUS_DQ ? "DQ" : status === STATUS_DNF ? "DNF" : "—";
    }
    const chestsCell = element("span", "feed-entrant__chests");
    const perHourCell = element("span", "feed-entrant__cph");
    const deltaCell = element("span", "feed-entrant__delta");
    const delta = ratingDeltaNode(raceIndex, entryIndex);
    if (delta) {
      deltaCell.appendChild(delta);
    }
    row.append(placeCell, nameCell, timeCell, chestsCell, perHourCell, deltaCell);
    return row;
  }

  function fillRaceComments(race, host) {
    const pending = UI.comments();
    if (!pending) {
      return;
    }
    pending.then((payload) => {
      const entries = payload && payload.races ? payload.races[race.r] : null;
      (entries || []).forEach(([userId, text]) => {
        const playerIndex = lookupPlayerIndex(userId);
        host.appendChild(UI.speech(text, { playerIndex: playerIndex ?? null, size: "compact" }));
      });
    });
  }

  function fillChestCells(race, rows) {
    const pending = UI.comments();
    if (!pending) {
      return;
    }
    pending.then((payload) => {
      race.e.forEach(([playerIndex, , status, seconds], entryIndex) => {
        const pace = status === 0 ? UI.chestPace(payload, race.r, participants.players[playerIndex].id, seconds) : null;
        if (!pace) {
          return;
        }
        const row = rows.children[entryIndex];
        row.querySelector(".feed-entrant__chests").append(String(pace.chests), element("span", "feed-entrant__unit", "CR"));
        row.querySelector(".feed-entrant__cph").append(String(pace.perHour), element("span", "feed-entrant__unit", "CPH"));
      });
    });
  }

  function buildDetail(race, raceIndex, detailId) {
    const detail = element("div", "feed-row__detail");
    detail.id = detailId;
    detail.hidden = true;
    const entrants = element("ol", "feed-entrants");
    race.e.forEach((entry, entryIndex) => entrants.appendChild(entrantRow(race, raceIndex, entry, entryIndex)));
    const link = element("a", "feed-row__external", "racetime.gg ↗");
    link.href = RACE_ROOT + race.r;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const comments = element("div", "feed-row__comments");
    detail.append(entrants, link, comments);
    fillChestCells(race, entrants);
    fillRaceComments(race, comments);
    return detail;
  }

  function dayLabel(isoDate) {
    const today = todayInZone();
    if (isoDate === today) return "TODAY";
    if (isoDate === shiftIsoDate(today, -1)) return "YESTERDAY";
    return dayFormatter.format(new Date(`${isoDate}T00:00:00Z`)).toUpperCase();
  }

  function raceRow(race, raceIndex) {
    const wrapper = element("div", "feed-row");
    const line = element("div", "feed-row__line");
    const detailId = `feed-detail-${raceIndex}`;

    const newDot = element("span", "feed-row__new");
    if (raceIndex >= firstNewIndex) {
      newDot.classList.add("feed-row__new--visible");
      newDot.setAttribute("role", "img");
      newDot.setAttribute("aria-label", "New");
    }
    const preset = race.p === null || race.p === undefined ? "" : participants.presets[race.p].n;
    const { cell: winner, winner: winnerEntry } = winnerCell(race);
    const time = element("span", "feed-row__time");
    time.appendChild(winnerEntry && winnerEntry[3] !== null ? UI.time(winnerEntry[3]) : document.createTextNode("—"));

    const toggle = element("button", "feed-row__toggle", "▾");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", detailId);
    toggle.setAttribute("aria-label", "Show entrants");

    line.append(
      newDot,
      element("span", "feed-row__hour", fullHour(race.h)),
      modeCell(race),
      element("span", "feed-row__preset", preset),
      winner,
      time,
      element("span", "feed-row__field", `${race.n} racers`),
      tagCell(race),
      toggle,
    );
    wrapper.appendChild(line);

    let detail = null;
    function toggleDetail() {
      if (!detail) {
        detail = buildDetail(race, raceIndex, detailId);
        wrapper.appendChild(detail);
      }
      const open = detail.hidden;
      detail.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      wrapper.classList.toggle("feed-row--open", open);
    }
    toggle.addEventListener("click", toggleDetail);
    line.addEventListener("click", (event) => {
      if (!event.target.closest("a, button, .feed-row__more")) {
        toggleDetail();
      }
    });
    return wrapper;
  }

  function renderMoreRows() {
    const races = participants.races;
    const end = Math.min(races.length, shownCount + PAGE_SIZE);
    for (let offset = shownCount; offset < end; offset += 1) {
      const raceIndex = races.length - 1 - offset;
      const race = races[raceIndex];
      if (race.d !== lastRenderedDay) {
        lastRenderedDay = race.d;
        feedList.appendChild(element("h3", "feed-day", dayLabel(race.d)));
      }
      feedList.appendChild(raceRow(race, raceIndex));
    }
    shownCount = end;
    moreButton.hidden = shownCount >= races.length;
  }

  function renderFeed() {
    const host = document.getElementById("latest-feed");
    feedList = element("div", "feed-list");
    moreButton = element("button", "mode-toggle__button feed-more", `Show ${PAGE_SIZE} more`);
    moreButton.type = "button";
    moreButton.addEventListener("click", renderMoreRows);
    host.append(feedList, moreButton);
    renderMoreRows();
  }

  // ---- Masthead comment ----

  function buildCommentPool(payload) {
    const cutoff = shiftIsoDate(todayInZone(), -HERO_WINDOW_DAYS);
    const all = [];
    Object.entries(payload.races).forEach(([raceName, entries]) => {
      const raceIndex = raceIndexByName.get(raceName);
      if (raceIndex === undefined) {
        return;
      }
      entries.forEach(([userId, text]) => {
        const playerIndex = lookupPlayerIndex(userId);
        if (playerIndex !== undefined) {
          all.push({ text, playerIndex, raceIndex });
        }
      });
    });
    // A quiet month widens the date window, never the length filter.
    const long = all.filter(({ text }) => text.length >= HERO_MIN_LENGTH);
    const recent = long.filter(({ raceIndex }) => participants.races[raceIndex].d >= cutoff);
    return recent.length >= HERO_MIN_POOL ? recent : long;
  }

  function heroBubble({ text, playerIndex, raceIndex }) {
    const race = participants.races[raceIndex];
    const entry = race.e.find((candidate) => candidate[0] === playerIndex);
    const shortText = text.length > HERO_MAX_LENGTH ? `${text.slice(0, HERO_MAX_LENGTH - 1).trimEnd()}…` : text;
    const context = [
      { text: playerName(playerIndex) },
      { text: modeName(race) },
      { text: entry ? placeText(race, entry) : "DNF" },
      { text: UI.relativeDay(race.d), href: RACE_ROOT + race.r },
    ];
    return UI.speech(shortText, { playerIndex, size: "hero", context });
  }

  async function renderHero() {
    const host = document.getElementById("comment-hero");
    const pending = UI.comments();
    const payload = pending ? await pending : null;
    if (!payload || !payload.races) {
      return;
    }
    const pool = buildCommentPool(payload);
    if (!pool.length) {
      return;
    }
    let current = null;

    function show() {
      let next = pool[Math.floor(Math.random() * pool.length)];
      while (pool.length > 1 && next === current) {
        next = pool[Math.floor(Math.random() * pool.length)];
      }
      current = next;
      const stage = element("div", "comment-hero__stage");
      stage.appendChild(heroBubble(current));
      const button = element("button", "comment-hero__refresh", "↻");
      button.type = "button";
      button.setAttribute("aria-label", "Another comment");
      button.addEventListener("click", show);
      stage.appendChild(button);
      host.replaceChildren(stage);
    }

    show();
  }

  // ---- Sidebar ----

  function sidebarSection(title) {
    const section = element("section", "feed-side");
    section.appendChild(element("h3", "feed-side__title", title));
    return section;
  }

  function rankMovementNode(deltas, playerIndex) {
    const delta = deltas.get(playerIndex);
    if (delta === undefined) {
      return null;
    }
    if (delta === "new") {
      return element("span", "trend trend--flat", "NEW");
    }
    return element("span", delta > 0 ? "trend trend--up" : "trend trend--down", `${delta > 0 ? "▲" : "▼"}${Math.abs(delta)}`);
  }

  function activeStreakTexts(playerIndex) {
    const streaks = participants.players[playerIndex].streaks;
    const texts = [];
    if (!streaks) {
      return texts;
    }
    const today = todayInZone();
    const daily = streaks.daily && streaks.daily.current;
    if (daily && daily[0] >= 2 && daily[2] >= shiftIsoDate(today, -1)) {
      texts.push(`${daily[0]}-day streak`);
    }
    const wins = streaks.win && streaks.win.current;
    if (wins && wins[0] >= 2 && participants.races[wins[2]].d >= daysAgoCutoff(RECENT_DAYS)) {
      texts.push(`${wins[0]} wins in a row`);
    }
    return texts;
  }

  function latestResultText(playerIndex) {
    const raceIndexes = raceIndexesByPlayer[playerIndex];
    if (!raceIndexes || !raceIndexes.length) {
      return null;
    }
    const race = participants.races[raceIndexes.at(-1)];
    const entry = race.e.find((candidate) => candidate[0] === playerIndex);
    return [placeText(race, entry), modeName(race), UI.relativeDay(race.d)].join(" · ");
  }

  function racerCard(playerIndex, deltas) {
    const card = element("div", "feed-racer");
    card.appendChild(UI.avatar(playerIndex, 32));
    const body = element("div", "feed-racer__body");
    const head = element("div", "feed-racer__head");
    head.append(playerLink(playerIndex, "racer-link"), UI.followStar(playerIndex));
    const movement = rankMovementNode(deltas, playerIndex);
    if (movement) {
      head.appendChild(movement);
    }
    body.appendChild(head);
    const latest = latestResultText(playerIndex);
    if (latest) {
      body.appendChild(element("p", "feed-racer__meta", latest));
    }
    const streaks = activeStreakTexts(playerIndex);
    if (streaks.length) {
      body.appendChild(element("p", "feed-racer__streak", streaks.join(" · ")));
    }
    card.appendChild(body);
    return card;
  }

  function renderYourRacers(host) {
    const section = sidebarSection("Your racers");
    const indexes = Following.ids().map(lookupPlayerIndex).filter((index) => index !== undefined);
    if (!indexes.length) {
      section.appendChild(element("p", "feed-side__empty", "☆ Star a racer to follow them here."));
    } else {
      const deltas = typeof rankDeltas === "function" ? rankDeltas(ALL_MODES) : new Map();
      indexes.forEach((playerIndex) => section.appendChild(racerCard(playerIndex, deltas)));
    }
    host.replaceChildren(section);
  }

  function renderFact(host) {
    const facts = participants.facts || [];
    if (!facts.length) {
      return;
    }
    const sentence = facts[fnv1a(todayInZone()) % facts.length];
    const section = sidebarSection("Fact of the day");
    const paragraph = element("p", "feed-fact");
    const number = sentence.match(/\d(?:[\d,.:]*\d)?/);
    if (number) {
      paragraph.append(
        document.createTextNode(sentence.slice(0, number.index)),
        element("span", "feed-fact__number", number[0]),
        document.createTextNode(sentence.slice(number.index + number[0].length)),
      );
    } else {
      paragraph.textContent = sentence;
    }
    section.appendChild(paragraph);
    host.appendChild(section);
  }

  function recentUnlocks() {
    const cutoff = daysAgoCutoff(NEW_BADGE_DAYS);
    const unlocks = [];
    const races = participants.races;
    for (let raceIndex = races.length - 1; raceIndex >= 0 && races[raceIndex].d >= cutoff; raceIndex -= 1) {
      (races[raceIndex].t || []).forEach((tag) => {
        if (tag[0] === "badge") {
          unlocks.push({ playerIndex: tag[1], badgeId: tag[2], raceIndex });
        } else if (tag[0] === "first_win") {
          unlocks.push({ playerIndex: tag[1], badgeId: "first_blood", raceIndex });
        }
      });
    }
    return unlocks.slice(0, NEW_BADGE_LIMIT);
  }

  function renderNewBadges(host) {
    const unlocks = recentUnlocks();
    if (!unlocks.length) {
      return;
    }
    const section = sidebarSection("New badges");
    const grid = element("div", "feed-badges");
    unlocks.forEach(({ playerIndex, badgeId, raceIndex }) => {
      const cell = element("div", "feed-badges__item");
      cell.append(UI.badge(badgeDefinition(badgeId), { earnedRaceIndex: raceIndex }), playerLink(playerIndex, "racer-link"));
      grid.appendChild(cell);
    });
    section.appendChild(grid);
    host.appendChild(section);
  }

  function renderSidebar() {
    const sidebar = document.getElementById("updates-sidebar-body");
    const racersHost = element("div", "feed-side-racers");
    sidebar.appendChild(racersHost);
    renderFact(sidebar);
    renderNewBadges(sidebar);
    const rerender = () => renderYourRacers(racersHost);
    rerender();
    document.addEventListener("racetimevibes:following", rerender);
  }

  // The toggle only shows below the sidebar breakpoint in feed.css, which also ignores
  // the open class on wide screens, so this never needs to know the viewport width.
  function bindSidebarToggle() {
    const layout = document.getElementById("updates-layout");
    const toggle = document.getElementById("updates-sidebar-toggle");
    toggle.addEventListener("click", () => {
      const open = layout.classList.toggle("updates-layout--open");
      toggle.setAttribute("aria-expanded", String(open));
    });
  }

  function init() {
    if (started) {
      return;
    }
    started = true;
    participants.races.forEach((race, index) => raceIndexByName.set(race.r, index));
    document.getElementById("updates").hidden = false;
    firstNewIndex = findFirstNewIndex(resolveBaseline());
    renderNewSummary();
    renderFeed();
    renderSidebar();
    bindSidebarToggle();
    renderHero();
  }

  return { init };
})();
