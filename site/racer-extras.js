const RacerExtras = (() => {
  const RIVAL_MIN_DECISIVE = 5;
  const RIVAL_AVATAR_SIZE = 28;
  const RATING_HEIGHT = 360;
  // No axis titles: the heading above the chart names the measure.
  const RATING_MARGIN = { top: 20, right: 18, bottom: 28, left: 44 };
  const RATING_CHART_ID = "player-rating";

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
    const label = element("span", "player-follow__label");
    label.dataset.playerId = player.id;
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
    label.textContent = Following.has(label.dataset.playerId) ? "Following" : "Follow";
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
    return UI.speech(comment.text, {
      size: "hero",
      context: [
        { text: modeLabel(participants.modes[race.m]) },
        { text: place },
        { text: UI.relativeDay(race.d), href: `${RACE_ROOT}${race.r}`, tooltip: formatRaceDate(race.d) },
      ],
    });
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

  function bestPresetTile(bestPreset) {
    const [presetIndex, ratio] = bestPreset;
    const preset = participants.presets[presetIndex];
    const faster = Math.round((1 - ratio) * 100);
    const item = tile("Best preset");
    item.append(element("strong", "pb-preset", preset.n));
    item.append(element("span", "pb-note", `${modeLabel(participants.modes[preset.m])} · ${faster > 0 ? `${faster}% faster than field` : "at field median"}`));
    return item;
  }

  function renderPbs(playerIndex) {
    const player = participants.players[playerIndex];
    const tiles = [];
    participants.modes.forEach((mode, modeIndex) => {
      const steps = player.pbs[String(modeIndex)];
      if (!steps || !steps.length) {
        return;
      }
      const [raceIndex, seconds] = steps[steps.length - 1];
      const item = tile(modeLabel(mode), mode);
      const value = element("strong");
      value.append(UI.time(seconds));
      item.append(value, element("span", "pb-note", formatRaceDate(participants.races[raceIndex].d)));
      tiles.push(item);
    });
    if (player.best_preset) {
      tiles.push(bestPresetTile(player.best_preset));
    }
    const host = document.querySelector("#player-pbs");
    host.hidden = !tiles.length;
    host.replaceChildren(...tiles);
  }

  function niceStep(range) {
    const steps = [10, 20, 25, 50, 100, 200, 250, 500];
    return steps.find((step) => range / step <= 6) || 1000;
  }

  // One point per race day. Races are in chronological order, so the day's last rated entry
  // holds the rating the racer closed the day on.
  function ratingDays(playerIndex) {
    const days = [];
    raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
      const race = participants.races[raceIndex];
      const rating = race.e[entryOf(race, playerIndex)][4];
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

  // A step line through each race day's closing rating, held to the archive's latest race as the
  // record charts hold a record. Only the first day and the current rating carry markers; the
  // pointer snaps to the nearest race day anywhere in the plot.
  function ratingChart(days, name, width) {
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
    const clip = svgElement("clipPath", { id: `${id}-plot` });
    clip.append(svgElement("rect", { x: margin.left, y: margin.top, width: plotWidth, height: plotHeight }));
    const defs = svgElement("defs");
    defs.append(clip);
    const span = days.length === 1
      ? `${current.rating} on ${formatRaceDate(current.date)}, their only race day`
      : `from ${first.rating} on ${formatRaceDate(first.date)} to ${current.rating} on ${formatRaceDate(current.date)}, over ${days.length} race days`;
    svg.append(
      svgElement("title", { id: `${id}-title` }, "Rating"),
      svgElement("desc", { id: `${id}-description` }, `${name}'s rating at the close of each race day, across all modes: ${span}.`),
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

  // The chart draws itself in once per racer, the first time it scrolls into view. A redraw
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

  function renderRating(playerIndex) {
    const days = ratingDays(playerIndex);
    const name = participants.players[playerIndex].name;
    const host = document.querySelector("#player-rating-chart");
    revealRating(host);
    mountChart(host, (width) => (days.length ? ratingChart(days, name, width) : emptyMessage("No rating yet.")));
  }

  function valueTile(label, ...value) {
    const item = tile(label);
    const strong = element("strong", "summary-value");
    strong.append(...value);
    item.append(strong);
    return item;
  }

  function rivalTile(label, matchup) {
    if (!matchup) {
      return valueTile(label, "--");
    }
    const player = participants.players[matchup.opponent];
    const item = tile(label);
    const row = element("div", "summary-value rival");
    const link = element("a", "rival__name", player.name);
    link.href = playerHash(player.id, ALL_MODES);
    row.append(UI.avatar(matchup.opponent, RIVAL_AVATAR_SIZE), link, element("strong", "rival__record", `${matchup.wins}-${matchup.losses}`));
    item.append(row);
    return item;
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

  // Every tile covers all modes and the whole archive, so the strip ignores the racer page's mode toggle.
  function renderSummary(playerIndex) {
    const player = participants.players[playerIndex];
    const board = participants.leaderboards.find((item) => item.mode === ALL_MODES);
    const row = board.rows.find((item) => item.p === playerIndex);
    const record = recordsForMode(ALL_MODES, ALL_RACES)[playerIndex];
    const rank = ratingRank(playerIndex);
    const rating =
      rank === undefined
        ? valueTile("Rank & Rating", "--")
        : valueTile("Rank & Rating", `#${integerFormatter.format(rank)}`, element("span", "summary-value__joiner", "with"), ratingText(player));
    if (rank !== undefined) {
      bindTooltip(rating, ratingTooltip());
    }
    const { nemesis, victim } = rivals(playerIndex);
    document.querySelector("#player-summary").replaceChildren(
      rating,
      valueTile("Podiums", row ? `${row.first} / ${row.second} / ${row.third}` : "--"),
      valueTile("Record W-L-D", `${record.wins}-${record.losses}-${record.draws}`),
      valueTile("H2H Win Rate", formatRate(winRate(record.wins, record.losses))),
      valueTile("Badges", `${Object.keys(player.badges).length} / ${participants.badges.length}`),
      rivalTile("Nemesis", nemesis),
      rivalTile("Favourite Victim", victim),
    );
  }

  function render(playerIndex) {
    if (renderedPlayer !== playerIndex) {
      renderedPlayer = playerIndex;
      renderHeading(playerIndex);
      renderSummary(playerIndex);
      renderBadges(playerIndex);
      renderPbs(playerIndex);
      renderRating(playerIndex);
    }
  }

  return { render, rivals, ratingDays };
})();
