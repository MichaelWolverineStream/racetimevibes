const Feed = (() => {
  const PAGE_SIZE = 20;
  // A racer page opens on their latest few races; "Show more" still adds a full page.
  const RACER_FIRST_PAGE = 5;
  const MAX_VISIBLE_TAGS = 4;
  const MAX_TAG_DETAILS = 5;
  const HERO_MIN_LENGTH = 12;
  const HERO_MIN_POOL = 20;
  const HERO_WINDOW_DAYS = 30;
  const HERO_MAX_LENGTH = 180;
  const NEW_BADGE_LIMIT = 12;
  const NEW_AVATAR_LIMIT = 6;
  const NEW_AVATAR_SIZE = 48;
  const FEATURED_WINDOW_DAYS = 14;
  const FEATURED_TOP_RANKS = 3;
  const FACT_REPEAT_DAYS = 7;
  // Seeds the fact shuffle. Changing it picks a new schedule from today on.
  const FACT_SEED = "fact of the day 2";
  const STATUS_DNF = 1;
  const STATUS_DQ = 2;
  const SUMMARY_TAGS = [
    ["pb", "PB"],
    ["record", "Mode record"],
    ["preset_record", "Preset record"],
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
  const rowDateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const monthFormatter = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

  let started = false;
  let firstNewIndex = 0;
  let racerShown = null;
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

  // 32-bit FNV-1a, so the fact order and the featured pick are stable for everyone on the same date.
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
    const details = pbTags.slice(0, MAX_TAG_DETAILS).map(([, playerIndex, seconds, previous]) => {
      const previousText = previous === null || previous === undefined ? "" : `, previous best ${formatDuration(previous)}`;
      return `${playerName(playerIndex)}: ${formatDuration(seconds)}${previousText}`;
    });
    if (pbTags.length > MAX_TAG_DETAILS) {
      details.push(`and ${pbTags.length - MAX_TAG_DETAILS} more`);
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
      case "preset_record":
        return {
          kind,
          label: "Preset record",
          detail: `${playerName(playerIndex)}: ${formatDuration(a)}, previous record ${formatDuration(b)}`,
        };
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
      default:
        return null;
    }
  }

  // Racers unlocking the same badge in one race share a pill, as PBs do.
  function badgeTag(sameBadge) {
    const definition = badgeDefinition(sameBadge[0][2]);
    const listed = sameBadge.slice(0, MAX_TAG_DETAILS).map(([, playerIndex]) => playerName(playerIndex)).join(", ");
    const names = sameBadge.length > MAX_TAG_DETAILS ? `${listed} and ${sameBadge.length - MAX_TAG_DETAILS} more` : listed;
    return {
      kind: "badge",
      label: sameBadge.length > 1 ? `${definition.label} ×${sameBadge.length}` : definition.label,
      detail: `${names} unlocked ${definition.label}: ${definition.description ?? ""} · ${UI.badgeShare(definition.id)}`,
    };
  }

  function raceTagDescriptions(race) {
    const tags = race.t || [];
    const pbTags = tags.filter((tag) => tag[0] === "pb");
    const badgeTagsById = new Map();
    tags.filter((tag) => tag[0] === "badge").forEach((tag) => {
      badgeTagsById.set(tag[2], [...(badgeTagsById.get(tag[2]) ?? []), tag]);
    });
    const descriptions = [];
    tags.forEach((tag) => {
      if (tag[0] === "pb") {
        if (tag === pbTags[0]) {
          descriptions.push(pbTag(pbTags));
        }
        return;
      }
      if (tag[0] === "badge") {
        const sameBadge = badgeTagsById.get(tag[2]);
        if (tag === sameBadge[0]) {
          descriptions.push(badgeTag(sameBadge));
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

  function tagCell(descriptions) {
    const cell = element("span", "feed-row__tags");
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

  // One tag on its own, for the entrant it names: PBs and badges are grouped only on the race row.
  function tagDescription(tag) {
    if (tag[0] === "pb") {
      return pbTag([tag]);
    }
    if (tag[0] === "badge") {
      return badgeTag([tag]);
    }
    return describeTag(tag);
  }

  function entrantTagDescriptions(race, playerIndex) {
    return (race.t || [])
      .filter((tag) => tag[1] === playerIndex)
      .map(tagDescription)
      .filter(Boolean);
  }

  function entrantTagCell(race, playerIndex) {
    const cell = element("span", "feed-entrant__tags");
    entrantTagDescriptions(race, playerIndex).forEach(({ kind, label, detail }) => cell.appendChild(UI.tag(kind, label, detail)));
    return cell;
  }

  function placePill(place) {
    const podium = place >= 1 && place <= 3 ? ` rank-badge--podium rank-badge--${place}` : "";
    return element("span", `rank-badge feed-place${podium}`, String(place));
  }

  function modeCell(race) {
    const name = modeName(race);
    const cell = element("span", "feed-row__mode");
    cell.append(UI.modeDot(name), element("span", null, modeLabel(name)));
    return cell;
  }

  function presetCell(race) {
    return element("span", "feed-row__preset", race.p === null || race.p === undefined ? "" : participants.presets[race.p].n);
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

  // A finisher's time, or why there is none.
  function resultTime(status, seconds) {
    if (status === 0 && seconds !== null) {
      return UI.time(seconds);
    }
    return document.createTextNode(status === STATUS_DQ ? "DQ" : status === STATUS_DNF ? "DNF" : "—");
  }

  function ratingDeltaNode(raceIndex, entryIndex) {
    const delta = typeof seasonModeRatingDelta === "function" ? seasonModeRatingDelta(raceIndex, entryIndex) : null;
    if (delta === null || delta === undefined) {
      return null;
    }
    // A points change, not a rank change: signed numbers, never the arrows that mean rank movement.
    const rounded = Math.round(delta);
    const node =
      rounded === 0
        ? element("span", "points-delta points-delta--flat", "\u00B10")
        : element(
            "span",
            rounded > 0 ? "points-delta points-delta--up" : "points-delta points-delta--down",
            `${rounded > 0 ? "+" : "\u2212"}${Math.abs(rounded)}`,
          );
    const race = participants.races[raceIndex];
    bindTooltip(node, `${seasonLabel(race.s)} ${modeLabel(participants.modes[race.m])} rating after this race: ${race.e[entryIndex][7]}`);
    return node;
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
    timeCell.appendChild(resultTime(status, seconds));
    const chestsCell = element("span", "feed-entrant__chests");
    const perHourCell = element("span", "feed-entrant__cph");
    const deltaCell = element("span", "feed-entrant__delta");
    const delta = ratingDeltaNode(raceIndex, entryIndex);
    if (delta) {
      deltaCell.appendChild(delta);
    }
    row.append(placeCell, nameCell, entrantTagCell(race, playerIndex), timeCell, chestsCell, perHourCell, deltaCell);
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

  // focus, when set, is the racer whose own row stands out among the entrants.
  function buildDetail(race, raceIndex, detailId, focus) {
    const detail = element("div", "feed-row__detail");
    detail.id = detailId;
    detail.hidden = true;
    const entrants = element("ol", "feed-entrants");
    race.e.forEach((entry, entryIndex) => {
      const row = entrantRow(race, raceIndex, entry, entryIndex);
      if (entry[0] === focus) {
        row.classList.add("feed-entrant--focus");
      }
      entrants.appendChild(row);
    });
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

  function dayHeading(race) {
    return { key: race.d, label: dayLabel(race.d) };
  }

  function monthHeading(race) {
    return { key: race.d.slice(0, 7), label: monthFormatter.format(new Date(`${race.d}T00:00:00Z`)).toUpperCase() };
  }

  // The home feed's row: the race and its winner.
  function winnerCells(race, raceIndex) {
    const newDot = element("span", "feed-row__new");
    if (raceIndex >= firstNewIndex) {
      newDot.classList.add("feed-row__new--visible");
      newDot.setAttribute("role", "img");
      newDot.setAttribute("aria-label", "New");
    }
    const { cell: winner, winner: winnerEntry } = winnerCell(race);
    const time = element("span", "feed-row__time");
    time.appendChild(winnerEntry && winnerEntry[3] !== null ? UI.time(winnerEntry[3]) : document.createTextNode("—"));
    return [
      newDot,
      element("span", "feed-row__hour", fullHour(race.h)),
      modeCell(race),
      presetCell(race),
      winner,
      time,
      element("span", "feed-row__field", `${race.n} racers`),
      tagCell(raceTagDescriptions(race)),
    ];
  }

  // The racer's comment and chest count live in comments.json, which loads separately, so they
  // fill in once it arrives. CR and CPH come from the same call as the expanded race's, so they match.
  function fillFocusCells(race, entry, commentCell, chestsCell) {
    const pending = UI.comments();
    if (!pending) {
      return;
    }
    pending.then((payload) => {
      const [playerIndex, , status, seconds] = entry;
      const userId = participants.players[playerIndex].id;
      const entries = payload && payload.races ? payload.races[race.r] : null;
      const own = (entries || []).find(([commenter]) => commenter === userId);
      if (own) {
        commentCell.textContent = own[1];
        bindTooltip(commentCell, own[1]);
      }
      const pace = status === 0 ? UI.chestPace(payload, race.r, userId, seconds) : null;
      if (pace) {
        const chests = element("span", null, String(pace.chests));
        chests.append(element("span", "feed-entrant__unit", "CR"));
        const perHour = element("span", null, String(pace.perHour));
        perHour.append(element("span", "feed-entrant__unit", "CPH"));
        chestsCell.append(chests, perHour);
      }
    });
  }

  // The racer page's row: the race and the racer's own result in it.
  function focusCells(race, raceIndex, playerIndex) {
    const entryIndex = race.e.findIndex((entry) => entry[0] === playerIndex);
    const entry = race.e[entryIndex];
    const [, place, status, seconds] = entry;
    const when = element("span", "feed-row__when", `${rowDateFormatter.format(new Date(`${race.d}T00:00:00Z`))} · ${fullHour(race.h)}`);
    const placeCell = element("span", "feed-row__place");
    if (status === 0 && place !== null) {
      placeCell.appendChild(placePill(place));
    }
    const comment = element("span", "feed-row__comment");
    const time = element("span", "feed-row__time");
    time.appendChild(resultTime(status, seconds));
    const chests = element("span", "feed-row__chests");
    const delta = element("span", "feed-row__delta");
    const deltaNode = ratingDeltaNode(raceIndex, entryIndex);
    if (deltaNode) {
      delta.appendChild(deltaNode);
    }
    fillFocusCells(race, entry, comment, chests);
    return [
      when,
      modeCell(race),
      presetCell(race),
      placeCell,
      comment,
      time,
      element("span", "feed-row__field", `${race.n} racers`),
      chests,
      tagCell(entrantTagDescriptions(race, playerIndex)),
      delta,
    ];
  }

  // focus is null for the home feed, whose rows show the winner, or the racer whose own result
  // each row shows. The prefix keeps detail ids unique when one race is on both lists.
  function raceRow(race, raceIndex, { idPrefix, focus }) {
    const wrapper = element("div", focus === null ? "feed-row" : "feed-row feed-row--racer");
    const line = element("div", "feed-row__line");
    const detailId = `${idPrefix}-detail-${raceIndex}`;

    const toggle = element("button", "feed-row__toggle", "▾");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", detailId);
    toggle.setAttribute("aria-label", "Show entrants");

    line.append(...(focus === null ? winnerCells(race, raceIndex) : focusCells(race, raceIndex, focus)), toggle);
    wrapper.appendChild(line);

    let detail = null;
    function toggleDetail() {
      if (!detail) {
        detail = buildDetail(race, raceIndex, detailId, focus);
        wrapper.appendChild(detail);
      }
      const open = detail.hidden;
      detail.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Hide entrants" : "Show entrants");
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

  // Lists the races, given newest first, with a heading wherever heading(race).key changes: firstPage
  // rows at first, then a page more per click. Each list keeps its own pages.
  function raceList(host, raceIndexes, { idPrefix, focus = null, heading, headingTag = "h3", firstPage = PAGE_SIZE }) {
    const list = element("div", "feed-list");
    const more = element("button", "mode-toggle__button feed-more", `Show ${PAGE_SIZE} more`);
    more.type = "button";
    let shown = 0;
    let lastKey = null;

    // Returns the first row added, or null when every race is already shown.
    function renderMore(count) {
      const end = Math.min(raceIndexes.length, shown + count);
      let firstRow = null;
      for (let offset = shown; offset < end; offset += 1) {
        const raceIndex = raceIndexes[offset];
        const race = participants.races[raceIndex];
        const { key, label } = heading(race);
        if (key !== lastKey) {
          lastKey = key;
          list.appendChild(element(headingTag, "feed-day", label));
        }
        const row = raceRow(race, raceIndex, { idPrefix, focus });
        firstRow ??= row;
        list.appendChild(row);
      }
      shown = end;
      more.hidden = shown >= raceIndexes.length;
      return firstRow;
    }

    // Focus moves to the first new row, so keyboard users carry on where the list grew.
    more.addEventListener("click", () => {
      const firstRow = renderMore(PAGE_SIZE);
      if (firstRow) {
        firstRow.querySelector(".feed-row__toggle").focus();
      }
    });
    host.replaceChildren(list, more);
    renderMore(firstPage);
  }

  function renderFeed() {
    const newestFirst = participants.races.map((race, raceIndex) => raceIndex).reverse();
    raceList(document.getElementById("latest-feed"), newestFirst, { idPrefix: "feed", heading: dayHeading });
  }

  // The racer page's Latest results, under month headings: most racers race about once a day. The
  // list covers every mode, so a mode change leaves it, and its pages, as they are.
  function racerResults(playerIndex) {
    if (playerIndex === racerShown) {
      return;
    }
    const newestFirst = [...raceIndexesByPlayer[playerIndex]].reverse();
    raceList(document.getElementById("player-results"), newestFirst, {
      idPrefix: "racer",
      focus: playerIndex,
      heading: monthHeading,
      headingTag: "h4",
      firstPage: RACER_FIRST_PAGE,
    });
    // Only once the list is built, so a failed render is retried on the next visit.
    racerShown = playerIndex;
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
      { text: modeLabel(modeName(race)) },
      { text: entry ? placeText(race, entry) : "DNF" },
      { text: UI.relativeDay(race.d), href: RACE_ROOT + race.r, tooltip: formatRaceDate(race.d) },
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

  // `season` names the scope of the ranks in the tooltip; null is all time.
  function rankMovementNode(deltas, playerIndex, season) {
    const delta = deltas.get(playerIndex);
    if (delta === undefined) {
      return null;
    }
    const node =
      delta === "new"
        ? element("span", "trend trend--flat", "NEW")
        : element("span", delta > 0 ? "trend trend--up" : "trend trend--down", `${delta > 0 ? "▲" : "▼"}${Math.abs(delta)}`);
    bindTooltip(node, `${season === null ? "All-time" : seasonLabel(season)} rank, last 7 days`);
    return node;
  }

  // The current season's overall rank movement; all time when the data has no seasons.
  function sidebarRankDeltas() {
    return typeof ratingRankDeltas === "function" ? ratingRankDeltas(ALL_MODES, currentSeason()) : new Map();
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
    return [placeText(race, entry), modeLabel(modeName(race)), UI.relativeDay(race.d)].join(" · ");
  }

  const CLAIM_PHRASES = {
    wins: (value, plus) => `${value}${plus} wins`,
    win_streak: (value, plus) => `${value}${plus} wins in a row`,
    podium_streak: (value, plus) => `${value}${plus} podiums in a row`,
    finish_streak: (value, plus) => `${value}${plus} finishes in a row`,
    races: (value, plus) => `${value}${plus} races`,
    daily: (value, plus) => `${value}${plus} race days in a row`,
    pbs: (value, plus) => `${value}${plus} personal bests`,
    opponents: (value, plus) => `${value}${plus} different opponents`,
    night: (value, plus) => `${value}${plus} finishes from midnight to 5 AM ET`,
    comeback: (value, plus) => `a comeback after ${value}${plus} days away`,
  };

  // The featured racer's claim to fame: [kind, value, count], where count racers match or beat it.
  function claimNode(claim) {
    if (!claim) {
      return null;
    }
    const [kind, value, count] = claim;
    const paragraph = element("p", "feed-racer__claim");
    if (kind !== "badge") {
      const phrase = CLAIM_PHRASES[kind];
      paragraph.textContent = count === 1
        ? `✦ Ladder best: ${phrase(integerFormatter.format(value), "")}`
        : `✦ 1 of ${integerFormatter.format(count)} racers with ${phrase(integerFormatter.format(value), "+")}`;
      return paragraph;
    }
    const definition = badgeDefinition(value);
    paragraph.appendChild(document.createTextNode(
      count === 1 ? "✦ The only racer with " : `✦ 1 of ${integerFormatter.format(count)} racers with `,
    ));
    const button = element("button", "feed-racer__badge");
    button.type = "button";
    button.setAttribute("aria-haspopup", "dialog");
    const image = element("img");
    image.src = definition.still;
    image.alt = "";
    image.width = 16;
    image.height = 16;
    button.append(image, document.createTextNode(definition.label));
    bindTooltip(button, definition.description);
    button.addEventListener("click", () => UI.badgePanel(definition));
    paragraph.appendChild(button);
    return paragraph;
  }

  function racerCard(playerIndex, deltas, { note = null, claim = null } = {}) {
    const season = currentSeason();
    const card = element("div", "feed-racer");
    card.appendChild(UI.avatar(playerIndex, 32));
    const body = element("div", "feed-racer__body");
    const head = element("div", "feed-racer__head");
    head.append(playerLink(playerIndex, "racer-link"), UI.followStar(playerIndex));
    const movement = rankMovementNode(deltas, playerIndex, season);
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
    if (note) {
      body.appendChild(element("p", "feed-racer__note", note));
    }
    const claimLine = claimNode(claim);
    if (claimLine) {
      body.appendChild(claimLine);
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
      const deltas = sidebarRankDeltas();
      indexes.forEach((playerIndex) => section.appendChild(racerCard(playerIndex, deltas)));
    }
    host.replaceChildren(section);
  }

  // Candidates come from races before today, so the pick holds all day as new results arrive;
  // it moves on only if the featured racer climbs into a top 3 today.
  function featuredRacer() {
    const today = todayInZone();
    const through = shiftIsoDate(today, -1);
    const from = shiftIsoDate(today, -FEATURED_WINDOW_DAYS);
    const latestHighlight = new Map();
    participants.races.forEach((race, raceIndex) => {
      if (race.d >= from && race.d <= through) {
        (race.t || []).forEach((tag) => latestHighlight.set(tag[1], { tag, raceIndex }));
      }
    });
    // A racer leading either the all-time or the current season's ladder in any category is already featured elsewhere.
    const categories = ratingCategories();
    const scopes = [null, currentSeason()].filter((season, index, all) => all.indexOf(season) === index);
    const inTopRanks = (rankMaps, playerIndex) => rankMaps.some((ranks) => ranks.get(playerIndex) <= FEATURED_TOP_RANKS);
    const ranksThrough = scopes.flatMap((season) => categories.map((modeId) => ratingRanksThrough(modeId, through, season)));
    const candidates = [...latestHighlight.keys()]
      .filter((playerIndex) => !inTopRanks(ranksThrough, playerIndex))
      .sort((left, right) => left - right);
    if (!candidates.length) {
      return null;
    }
    const ranksNow = scopes.flatMap((season) => categories.map((modeId) => ratingRanks(modeId, season)));
    const start = fnv1a(`featured ${today}`) % candidates.length;
    for (let step = 0; step < candidates.length; step += 1) {
      const playerIndex = candidates[(start + step) % candidates.length];
      if (!inTopRanks(ranksNow, playerIndex)) {
        return { playerIndex, ...latestHighlight.get(playerIndex) };
      }
    }
    return null;
  }

  function highlightLabel(tag, race) {
    switch (tag[0]) {
      case "badge":
        return `Unlocked ${badgeDefinition(tag[2]).label}`;
      case "first_win":
        return "First win";
      case "pb":
        return `${modeLabel(modeName(race))} PB`;
      case "record":
        return `${modeLabel(modeName(race))} record`;
      case "preset_record":
        return `${participants.presets[race.p].n} record`;
      case "upset":
        return "Upset win";
      case "photo":
        return "Won a photo finish";
      case "milestone":
        return `${ordinal(tag[2])} race`;
      default:
        return "New highlight";
    }
  }

  function renderFeatured(host) {
    const featured = featuredRacer();
    if (!featured) {
      return;
    }
    const race = participants.races[featured.raceIndex];
    const note = `★ ${highlightLabel(featured.tag, race)} · ${UI.relativeDay(race.d)}`;
    const deltas = sidebarRankDeltas();
    const section = sidebarSection("Featured daily racer");
    section.appendChild(racerCard(featured.playerIndex, deltas, { note, claim: participants.players[featured.playerIndex].claim }));
    host.appendChild(section);
  }

  // One cycle's shuffle of the fact indexes. The cycle and index lead the hashed text,
  // so FNV-1a mixes them through the rest of it.
  function factShuffle(count, cycle) {
    const order = [...Array(count).keys()];
    return order.sort((a, b) => fnv1a(`${cycle} ${a} ${FACT_SEED}`) - fnv1a(`${cycle} ${b} ${FACT_SEED}`) || a - b);
  }

  // Every fact shows once in each cycle of facts.length days, in an order reshuffled every cycle.
  // A shown fact then stays away for FACT_REPEAT_DAYS days: the facts that closed the previous
  // cycle move to the back of this cycle's first 2 × away slots, so none can open it. Only those
  // slots move, so with 3 × away facts or more a cycle still closes as its plain shuffle does,
  // and no past day needs remembering.
  function factOfTheDay(facts) {
    const day = dayNumber(todayInZone());
    const cycle = Math.floor(day / facts.length);
    const away = Math.min(FACT_REPEAT_DAYS, Math.floor(facts.length / 3));
    const closing = new Set(factShuffle(facts.length, cycle - 1).slice(facts.length - away));
    const order = factShuffle(facts.length, cycle);
    const head = order.slice(0, 2 * away);
    const settled = [
      ...head.filter((index) => !closing.has(index)),
      ...head.filter((index) => closing.has(index)),
      ...order.slice(2 * away),
    ];
    return facts[settled[day % facts.length]];
  }

  function renderFact(host) {
    const facts = participants.facts || [];
    if (!facts.length) {
      return;
    }
    const [before, headline, after] = factOfTheDay(facts);
    const section = sidebarSection("Fact of the day");
    const paragraph = element("p", "feed-fact");
    paragraph.append(
      document.createTextNode(before),
      element("span", "feed-fact__number", headline),
      document.createTextNode(after),
    );
    section.appendChild(paragraph);
    host.appendChild(section);
  }

  function latestUnlocks() {
    const unlocks = [];
    const races = participants.races;
    for (let raceIndex = races.length - 1; raceIndex >= 0 && unlocks.length < NEW_BADGE_LIMIT; raceIndex -= 1) {
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
    const unlocks = latestUnlocks();
    if (!unlocks.length) {
      return;
    }
    const section = sidebarSection("Latest badges");
    const grid = element("div", "feed-badges");
    unlocks.forEach(({ playerIndex, badgeId, raceIndex }) => {
      const cell = element("div", "feed-badges__item");
      cell.append(UI.badge(badgeDefinition(badgeId), { earnedRaceIndex: raceIndex }), playerLink(playerIndex, "racer-link"));
      grid.appendChild(cell);
    });
    section.appendChild(grid);
    host.appendChild(section);
  }

  // avatar_changes is newest first: [playerIndex, Eastern date the archive first saw the avatar].
  function renderLatestAvatars(host) {
    const changes = (participants.avatar_changes || []).slice(0, NEW_AVATAR_LIMIT);
    if (!changes.length) {
      return;
    }
    const section = sidebarSection("Latest avatars");
    const grid = element("div", "feed-avatars");
    changes.forEach(([playerIndex, isoDate]) => {
      const cell = element("div", "feed-avatars__item");
      const when = element("span", "feed-avatars__when", UI.relativeDay(isoDate));
      when.tabIndex = 0;
      bindTooltip(when, `First seen ${formatRaceDate(isoDate)}`);
      cell.append(UI.avatar(playerIndex, NEW_AVATAR_SIZE), playerLink(playerIndex, "racer-link"), when);
      grid.appendChild(cell);
    });
    section.appendChild(grid);
    host.appendChild(section);
  }

  function renderSidebar() {
    const sidebar = document.getElementById("updates-sidebar-body");
    const racersHost = element("div", "feed-side-racers");
    sidebar.appendChild(racersHost);
    renderFeatured(sidebar);
    renderFact(sidebar);
    renderNewBadges(sidebar);
    renderLatestAvatars(sidebar);
    const rerender = () => renderYourRacers(racersHost);
    rerender();
    document.addEventListener(FOLLOWING_EVENT, rerender);
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

  return { init, racerResults };
})();
