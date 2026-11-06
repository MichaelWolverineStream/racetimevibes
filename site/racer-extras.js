const RacerExtras = (() => {
  const RIVAL_MIN_DECISIVE = 5;
  const RIVAL_AVATAR_SIZE = 28;
  const RATING_HEIGHT = 360;
  // No axis titles: the heading above the chart names the measure.
  const RATING_MARGIN = { top: 20, right: 18, bottom: 28, left: 44 };
  const RATING_CHART_ID = "player-rating";
  // Keeps the heading's bubble to about two lines; the whole comment shows on hover.
  const QUOTE_MAX_LENGTH = 80;

  let renderedPlayer = null;
  let quoteToken = 0;
  let followListening = false;
  let ratingObserver = null;

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

  function entryOf(race, playerIndex) {
    return race.e.findIndex((entry) => entry[0] === playerIndex);
  }

  function tile(label, mode = null) {
    const item = element("div", "summary-item");
    const caption = element("span", "summary-label");
    if (mode) {
      caption.append(UI.modeDot(mode));
    }
    caption.append(label);
    item.append(caption);
    return item;
  }

  function renderFollow(playerIndex) {
    const player = participants.players[playerIndex];
    const star = UI.followStar(playerIndex);
    // Both words share one grid cell, so the label keeps the longer word's width and following a
    // racer never shifts the star, which sits at the right end of the heading's top line.
    const label = element("span", "player-follow__label");
    label.dataset.playerId = player.id;
    label.append(element("span", "player-follow__word player-follow__word--off", "Follow"), element("span", "player-follow__word player-follow__word--on", "Following"));
    label.addEventListener("click", () => star.click());
    syncFollowLabel(label);
    document.querySelector("#player-follow").replaceChildren(star, label);
    if (!followListening) {
      followListening = true;
      document.addEventListener(FOLLOWING_EVENT, () => {
        const current = document.querySelector("#player-follow .player-follow__label");
        if (current) {
          syncFollowLabel(current);
        }
      });
    }
  }

  function syncFollowLabel(label) {
    label.dataset.following = String(Following.has(label.dataset.playerId));
  }

  function commentsFor(data, playerIndex) {
    const id = participants.players[playerIndex].id;
    const found = [];
    const raceIndexes = raceIndexesByPlayer[playerIndex];
    for (let index = raceIndexes.length - 1; index >= 0; index -= 1) {
      const race = participants.races[raceIndexes[index]];
      const match = (data.races[race.r] || []).find((comment) => comment[0] === id);
      if (match) {
        found.push({ raceIndex: raceIndexes[index], text: match[1] });
      }
    }
    return found;
  }

  function quoteBubble(comment, playerIndex) {
    const race = participants.races[comment.raceIndex];
    const entry = race.e[entryOf(race, playerIndex)];
    const place = isFinisher(entry) ? ordinal(entry[1]) : entry[2] === DQ_STATUS ? "DQ" : "DNF";
    const cut = comment.text.length > QUOTE_MAX_LENGTH;
    const shown = cut ? `${comment.text.slice(0, QUOTE_MAX_LENGTH - 1).trimEnd()}…` : comment.text;
    const speech = UI.speech(shown, {
      size: "hero",
      context: [
        { text: modeLabel(participants.modes[race.m]) },
        { text: place },
        { text: UI.relativeDay(race.d), href: `${RACE_ROOT}${race.r}`, tooltip: formatRaceDate(race.d) },
      ],
    });
    if (cut) {
      bindTooltip(speech.querySelector(".ui-speech__bubble"), comment.text);
    }
    return speech;
  }

  function renderQuote(playerIndex) {
    const host = document.querySelector("#player-quote");
    const token = ++quoteToken;
    host.replaceChildren();
    UI.comments().then((data) => {
      if (token !== quoteToken || !data) {
        return;
      }
      const comments = commentsFor(data, playerIndex);
      if (!comments.length) {
        return;
      }
      let shown = 0;
      const row = element("div", "player-quote__row");
      const draw = () => {
        const bubble = quoteBubble(comments[shown], playerIndex);
        const current = row.querySelector(".ui-speech");
        if (current) {
          current.replaceWith(bubble);
        } else {
          row.prepend(bubble);
        }
      };
      draw();
      if (comments.length > 1) {
        const next = element("button", "player-quote__next", "↻");
        next.type = "button";
        next.setAttribute("aria-label", "Another comment");
        next.addEventListener("click", () => {
          shown = (shown + 1) % comments.length;
          draw();
        });
        row.append(next);
      }
      host.append(row);
    });
  }

  function renderHeading(playerIndex) {
    document.querySelector("#player-avatar").replaceChildren(UI.avatar(playerIndex, 64));
    renderFollow(playerIndex);
    document.querySelector("#player-share").replaceChildren(ShareCard.button(playerIndex));
    renderQuote(playerIndex);
  }

  function renderBadges(playerIndex) {
    const player = participants.players[playerIndex];
    const earnedAt = (definition) => player.badges[definition.id];
    // Earned badges in the order they were won, oldest first; locked ones follow in table order.
    const earned = participants.badges
      .filter((definition) => earnedAt(definition) !== undefined)
      .sort((a, b) => earnedAt(a) - earnedAt(b));
    const locked = participants.badges.filter((definition) => earnedAt(definition) === undefined);
    const ordered = [...earned, ...locked];
    document.querySelector("#player-badges").replaceChildren(
      ...ordered.map((definition) =>
        UI.badge(definition, {
          earnedRaceIndex: player.badges[definition.id] ?? null,
          progress: player.progress[definition.id] ?? null,
        }),
      ),
    );
  }

  // The rank and provisional mark follow the same rules as the rating chart's standing line.
  function ratingTile(playerIndex, modeId) {
    const rating = ratingFor(participants.players[playerIndex], modeId);
    const item = modeId === ALL_MODES ? tile("Overall rating") : tile(modeLabel(modeId), modeId);
    item.append(element("strong", null, ratingText(rating)));
    if (rating === null) {
      item.append(element("span", "rating-note", "Unrated"));
      return item;
    }
    const rank = `#${integerFormatter.format(ratingRank(playerIndex, modeId))} of ${integerFormatter.format(ratingRanks(modeId).size)}`;
    const provisional = rating.finishes < participants.rating.provisional_finishes ? " · provisional" : "";
    item.append(element("span", "rating-note", `${rank}${provisional}`));
    bindTooltip(item, ratingTooltip(modeId));
    return item;
  }

  // Overall always; a mode only once the racer has a rating there.
  function renderRatings(playerIndex) {
    const player = participants.players[playerIndex];
    const categories = ratingCategories().filter((modeId) => modeId === ALL_MODES || ratingFor(player, modeId) !== null);
    document.querySelector("#player-ratings").replaceChildren(...categories.map((modeId) => ratingTile(playerIndex, modeId)));
  }

  function niceStep(range) {
    const steps = [10, 20, 25, 50, 100, 200, 250, 500];
    return steps.find((step) => range / step <= 6) || 1000;
  }

  // One point per race day. Races are in chronological order, so the day's last rated entry
  // holds the rating the racer closed the day on.
  // For one mode, only that mode's races count and the rating is the racer's rating in the mode.
  function ratingDays(playerIndex, modeId = ALL_MODES) {
    const days = [];
    const modeIndex = modeIndexFor(modeId);
    const ratingIndex = modeIndex === null ? 4 : 5;
    raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
      const race = participants.races[raceIndex];
      if (modeIndex !== null && race.m !== modeIndex) {
        return;
      }
      const rating = race.e[entryOf(race, playerIndex)][ratingIndex];
      if (rating === null || rating === undefined) {
        return;
      }
      let day = days.at(-1);
      if (!day || day.date !== race.d) {
        day = { date: race.d, day: dayNumber(race.d), rating, races: 0, modes: [] };
        days.push(day);
      }
      day.rating = rating;
      day.races += 1;
      if (!day.modes.includes(race.m)) {
        day.modes.push(race.m);
      }
    });
    days.forEach((day, index) => {
      day.delta = day.rating - (index ? days[index - 1].rating : participants.rating.base);
    });
    return days;
  }

  function ratingDayLabel(day) {
    const change = day.delta ? ` ${day.delta > 0 ? "▲" : "▼"}${Math.abs(day.delta)}` : "";
    const modes = day.modes.map((mode) => modeLabel(participants.modes[mode])).join(", ");
    const races = day.races === 1 ? modes : `${day.races} races (${modes})`;
    return `${formatRaceDate(day.date)} · ${day.rating}${change} · ${races}`;
  }

  // The index of the value closest to target in an ascending list.
  function nearestIndex(values, target) {
    let low = 0;
    let high = values.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (values[middle] < target) {
        low = middle + 1;
      } else {
        high = middle;
      }
    }
    return low > 0 && target - values[low - 1] < values[low] - target ? low - 1 : low;
  }

  // The chart and heading title for the overall rating or one mode's.
  function ratingTitle(modeId) {
    return modeId === ALL_MODES ? "Rating" : `${modeLabel(modeId)} rating`;
  }

  // A step line through each race day's closing rating, held to the archive's latest race as the
  // record charts hold a record. Only the first day and the current rating carry markers; the
  // pointer snaps to the nearest race day anywhere in the plot.
  function ratingChart(days, name, width, modeId) {
    const height = RATING_HEIGHT;
    const margin = RATING_MARGIN;
    const id = RATING_CHART_ID;
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const plotBottom = margin.top + plotHeight;
    const base = participants.rating.base;
    const values = days.map((day) => day.rating).concat(base);
    const step = niceStep(Math.max(...values) - Math.min(...values));
    // Half a step of room below the lowest value, so the first day's dot and the wash never sit on the axis.
    const low = Math.floor((Math.min(...values) - step / 2) / step) * step;
    const high = Math.ceil(Math.max(...values) / step) * step;
    const first = days[0];
    const current = days.at(-1);
    const latest = dayNumber(participants.races.at(-1).d);
    const pad = Math.max(1, (latest - first.day) * 0.03);
    const start = first.day - pad;
    const end = latest + pad;
    const x = (day) => margin.left + ((day - start) / (end - start)) * plotWidth;
    const y = (value) => plotBottom - ((value - low) / (high - low)) * plotHeight;

    const svg = svgElement("svg", {
      class: "chart-svg step-chart",
      width,
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-labelledby": `${id}-title ${id}-description`,
    });
    if (modeId !== ALL_MODES) {
      svg.style.setProperty("--step-color", modeColor(modeId));
    }
    const clip = svgElement("clipPath", { id: `${id}-plot` });
    clip.append(svgElement("rect", { x: margin.left, y: margin.top, width: plotWidth, height: plotHeight }));
    const defs = svgElement("defs");
    defs.append(clip);
    const title = ratingTitle(modeId);
    const scope = modeId === ALL_MODES ? "across all modes" : `in ${modeLabel(modeId)} races`;
    const span = days.length === 1
      ? `${current.rating} on ${formatRaceDate(current.date)}, their only race day`
      : `from ${first.rating} on ${formatRaceDate(first.date)} to ${current.rating} on ${formatRaceDate(current.date)}, over ${days.length} race days`;
    svg.append(
      svgElement("title", { id: `${id}-title` }, title),
      svgElement("desc", { id: `${id}-description` }, `${name}'s rating at the close of each race day, ${scope}: ${span}.`),
      defs,
    );
    for (let tick = low; tick <= high; tick += step) {
      svg.append(
        svgElement("line", { class: tick === low ? "baseline" : "grid-line", x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick) }),
        svgElement("text", { class: "axis-label", x: margin.left - 10, y: y(tick) + 4, "text-anchor": "end" }, tick),
      );
    }
    dateTicks(Math.ceil(start), Math.floor(end), plotWidth).forEach((tick) => {
      svg.append(svgElement("text", { class: "axis-label", x: x(tick.day), y: plotBottom + 18, "text-anchor": "middle" }, tick.label));
    });

    const xs = days.map((day) => x(day.day));
    const { wash, line, at } = stepLine(defs, {
      id,
      points: days.map((day, index) => [xs[index], y(day.rating)]),
      endX: x(latest),
      plotBottom,
      clipPath: `url(#${id}-plot)`,
    });
    const guide = svgElement("line", { class: "rating-chart__guide", y1: margin.top, y2: plotBottom });
    const dot = svgElement("circle", { class: "rating-chart__dot", r: STEP_CURRENT_RADIUS });
    const cursor = svgElement("g", { class: "rating-chart__cursor", hidden: "" });
    cursor.append(guide, dot);
    svg.append(
      wash,
      svgElement("line", { class: "rating-reference", x1: margin.left, x2: width - margin.right, y1: y(base), y2: y(base) }),
      line,
      cursor,
    );
    const marked = days.length === 1 ? [0] : [0, days.length - 1];
    marked.forEach((index) => {
      const kind = index === days.length - 1 ? "current" : "first";
      const mark = stepMark({ cx: xs[index], cy: y(days[index].rating), kind, label: ratingDayLabel(days[index]), at: at[index] });
      mark.dataset.i = index;
      svg.appendChild(mark);
    });

    // Over a marker the pointer takes that marker's day; anywhere else in the plot, the nearest one.
    svg.addEventListener("mousemove", (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = event.clientX - bounds.left;
      const pointerY = event.clientY - bounds.top;
      const mark = event.target.closest(".chart-mark");
      const inPlot = pointerX >= margin.left && pointerX <= width - margin.right && pointerY >= margin.top && pointerY <= plotBottom;
      if (!mark && !inPlot) {
        cursor.setAttribute("hidden", "");
        hideTooltip();
        return;
      }
      const index = mark ? Number(mark.dataset.i) : nearestIndex(xs, pointerX);
      guide.setAttribute("x1", xs[index]);
      guide.setAttribute("x2", xs[index]);
      dot.setAttribute("cx", xs[index]);
      dot.setAttribute("cy", y(days[index].rating));
      cursor.removeAttribute("hidden");
      showTooltip(dot, ratingDayLabel(days[index]), event);
    });
    svg.addEventListener("mouseleave", () => {
      cursor.setAttribute("hidden", "");
      hideTooltip();
    });
    return svg;
  }

  // The chart draws itself in each time it is rebuilt for a racer or mode, the first time it scrolls
  // into view; the observer unobserves after each reveal, so the next one observes again. A redraw
  // after that, as on resize, inserts finished nodes, so nothing replays.
  function revealRating(host) {
    if (!ratingObserver) {
      ratingObserver = new IntersectionObserver((entries) => {
        entries.filter((entry) => entry.isIntersecting).forEach((entry) => {
          ratingObserver.unobserve(entry.target);
          requestAnimationFrame(() => entry.target.classList.remove("step-chart-host--pending"));
        });
      }, { threshold: 0.4 });
    }
    host.classList.add("step-chart-host--pending");
    ratingObserver.observe(host);
  }

  // The heading, standing line and chart follow the racer page's mode picker.
  function renderRating(playerIndex, modeId = ALL_MODES) {
    const player = participants.players[playerIndex];
    const rating = ratingFor(player, modeId);
    const rank = ratingRank(playerIndex, modeId);
    document.querySelector("#player-rating-title").textContent = ratingTitle(modeId);
    const standing = document.querySelector("#player-rating-standing");
    standing.hidden = rating === null;
    standing.textContent = rating === null
      ? ""
      : `#${integerFormatter.format(rank)} of ${integerFormatter.format(ratingRanks(modeId).size)} · ${rating.rating}${
          rating.finishes < participants.rating.provisional_finishes ? " · provisional" : ""
        }`;
    const days = ratingDays(playerIndex, modeId);
    const host = document.querySelector("#player-rating-chart");
    revealRating(host);
    mountChart(host, (width) => (days.length ? ratingChart(days, player.name, width, modeId) : emptyMessage("No rating yet.")));
  }

  // The heading's stats card: each tile is a label over a value, and some add a meter under it.
  function stat(label, value, className = "player-stat", extra = null) {
    const item = element("div", className);
    item.append(element("span", "summary-label", label), value);
    if (extra) {
      item.append(extra);
    }
    return item;
  }

  // A thin bar filled to a share from 0 to 1, or nothing when there is no share.
  function meter(fraction) {
    if (fraction === null) {
      return null;
    }
    const bar = element("span", "player-stat__meter");
    bar.setAttribute("aria-hidden", "true");
    bar.style.setProperty("--fill", String(fraction));
    return bar;
  }

  // Firsts, seconds and thirds in the leaderboard's medal colours.
  function podiums(row) {
    const value = element("strong", "player-stat__value");
    if (!row) {
      value.textContent = "--";
      return value;
    }
    const counts = [["first", row.first], ["second", row.second], ["third", row.third]];
    counts.forEach(([medal, count], index) => {
      if (index) {
        value.append(element("span", "player-stat__sep", "·"));
      }
      value.append(element("span", `medal-count--${medal}`, String(count)));
    });
    return value;
  }

  function rivalStat(label, matchup, outcome) {
    if (!matchup) {
      return stat(label, element("strong", "player-stat__value", "--"), "player-stat player-stat--rival");
    }
    const player = participants.players[matchup.opponent];
    const row = element("div", "rival");
    const link = element("a", "rival__name", player.name);
    link.href = playerHash(player.id, ALL_MODES);
    const record = element("strong", `rival__record rival__record--${outcome}`, `${matchup.wins}-${matchup.losses}`);
    row.append(UI.avatar(matchup.opponent, RIVAL_AVATAR_SIZE), link, record);
    return stat(label, row, "player-stat player-stat--rival");
  }

  function rivals(playerIndex) {
    const candidates = computeHeadToHead(playerIndex, ALL_MODES)
      .filter((matchup) => matchup.wins + matchup.losses >= RIVAL_MIN_DECISIVE)
      .map((matchup) => ({ ...matchup, rate: matchup.wins / (matchup.wins + matchup.losses) }));
    const pick = (list, order) => list.sort((left, right) => order * (left.rate - right.rate) || right.meetings - left.meetings)[0];
    return {
      nemesis: pick(candidates.filter((matchup) => matchup.rate < 0.5), 1),
      victim: pick(candidates.filter((matchup) => matchup.rate > 0.5), -1),
    };
  }

  // Every stat covers all modes and the whole archive, so the heading ignores the racer page's mode toggle.
  function renderSummary(playerIndex) {
    const player = participants.players[playerIndex];
    const board = participants.leaderboards.find((item) => item.mode === ALL_MODES);
    const row = board.rows.find((item) => item.p === playerIndex);
    const record = recordsForMode(ALL_MODES, ALL_RACES)[playerIndex];
    const value = (text) => element("strong", "player-stat__value", text);
    const { nemesis, victim } = rivals(playerIndex);
    const rate = winRate(record.wins, record.losses);
    const earned = Object.keys(player.badges).length;
    const total = participants.badges.length;
    document.querySelector("#player-summary").replaceChildren(
      stat("Podiums", podiums(row)),
      stat("H2H Win Rate", value(formatRate(rate)), "player-stat", meter(rate)),
      stat("Badges", value(`${earned} / ${total}`), "player-stat", meter(total ? earned / total : null)),
      rivalStat("Nemesis", nemesis, "loss"),
      rivalStat("Favourite Victim", victim, "win"),
    );
  }

  function render(playerIndex) {
    if (renderedPlayer !== playerIndex) {
      renderedPlayer = playerIndex;
      renderHeading(playerIndex);
      renderSummary(playerIndex);
      renderBadges(playerIndex);
      renderRatings(playerIndex);
    }
  }

  return { render, renderRating, rivals, ratingDays };
})();
