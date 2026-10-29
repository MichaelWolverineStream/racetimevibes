const RacerExtras = (() => {
  const RIVAL_MIN_DECISIVE = 5;
  const RATING_HEIGHT = 360;
  const RATING_MARGIN = { top: 20, right: 18, bottom: 46, left: 62 };
  const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

  let renderedPlayer = null;
  let quoteToken = 0;
  let followListening = false;

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

  function utcDate(isoDate) {
    return new Date(`${isoDate}T00:00:00Z`);
  }

  function entryOf(race, playerIndex) {
    return race.e.findIndex((entry) => entry[0] === playerIndex);
  }

  function tile(label, dotColor = null) {
    const item = element("div", "summary-item");
    const caption = element("span", "summary-label");
    if (dotColor) {
      const dot = element("span", "mode-dot");
      dot.style.background = dotColor;
      caption.append(dot);
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
      document.addEventListener("racetimevibes:following", () => {
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
        { text: UI.relativeDay(race.d), href: `${RACE_ROOT}${race.r}` },
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
      const item = tile(modeLabel(mode), modeColor(mode));
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

  function ratingPoints(playerIndex) {
    const points = [];
    raceIndexesByPlayer[playerIndex].forEach((raceIndex) => {
      const race = participants.races[raceIndex];
      const entryIndex = entryOf(race, playerIndex);
      const rating = race.e[entryIndex][4];
      if (rating !== null && rating !== undefined) {
        points.push({ raceIndex, entryIndex, rating, day: dayNumber(race.d) });
      }
    });
    return points;
  }

  function ratingLabel(point) {
    const race = participants.races[point.raceIndex];
    const delta = typeof ratingDelta === "function" ? ratingDelta(point.raceIndex, point.entryIndex) : null;
    let change = "";
    if (delta) {
      change = ` (${delta > 0 ? "▲" : "▼"}${Math.abs(delta)})`;
    }
    return `${shortDate.format(utcDate(race.d))} · ${point.rating}${change} · ${modeLabel(participants.modes[race.m])}`;
  }

  function ratingChart(points, name, width) {
    const height = RATING_HEIGHT;
    const margin = RATING_MARGIN;
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const base = participants.rating.base;
    const values = points.map((point) => point.rating).concat(base);
    const step = niceStep(Math.max(...values) - Math.min(...values));
    const low = Math.floor(Math.min(...values) / step) * step;
    // A racer who never had a decided pair still sits exactly on the base, so give the axis room.
    const high = Math.max(Math.ceil(Math.max(...values) / step) * step, low + step);
    const first = points[0].day;
    const last = points.at(-1).day;
    const pad = Math.max(1, (last - first) * 0.03);
    const x = (day) => margin.left + ((day - (first - pad)) / (last - first + 2 * pad)) * plotWidth;
    const y = (value) => margin.top + plotHeight - ((value - low) / (high - low)) * plotHeight;

    const svg = svgElement("svg", {
      class: "chart-svg",
      width,
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-labelledby": "player-rating-title player-rating-description",
    });
    svg.append(
      svgElement("title", { id: "player-rating-title" }, "Rating"),
      svgElement("desc", { id: "player-rating-description" }, `${name}'s rating after each race, across all modes.`),
    );
    for (let tick = low; tick <= high; tick += step) {
      svg.append(
        svgElement("line", { class: tick === low ? "baseline" : "grid-line", x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick) }),
        svgElement("text", { class: "axis-label", x: margin.left - 10, y: y(tick) + 4, "text-anchor": "end" }, tick),
      );
    }
    dateTicks(Math.ceil(first - pad), Math.floor(last + pad)).forEach((tick) => {
      svg.append(svgElement("text", { class: "axis-label", x: x(tick.day), y: margin.top + plotHeight + 18, "text-anchor": "middle" }, tick.label));
    });
    const titleY = margin.top + plotHeight / 2;
    svg.append(
      svgElement("text", { class: "axis-label", x: 14, y: titleY, transform: `rotate(-90 14 ${titleY})`, "text-anchor": "middle" }, "RATING"),
      svgElement("text", { class: "axis-label", x: margin.left + plotWidth / 2, y: height - 8, "text-anchor": "middle" }, "RACE DATE"),
      svgElement("line", { class: "rating-reference", x1: margin.left, x2: width - margin.right, y1: y(base), y2: y(base) }),
      svgElement("polyline", { class: "rating-line", points: points.map((point) => `${x(point.day)},${y(point.rating)}`).join(" ") }),
      ...points.map((point, index) => svgElement("circle", { class: "trend-dot", cx: x(point.day), cy: y(point.rating), r: 3, "data-i": index })),
    );
    svg.addEventListener("mousemove", (event) => {
      const dot = event.target.closest("circle[data-i]");
      if (dot) {
        showTooltip(dot, ratingLabel(points[dot.dataset.i]), event);
      } else {
        hideTooltip();
      }
    });
    svg.addEventListener("mouseleave", hideTooltip);
    return svg;
  }

  function renderRating(playerIndex) {
    const points = ratingPoints(playerIndex);
    const name = participants.players[playerIndex].name;
    mountChart(document.querySelector("#player-rating-chart"), (width) =>
      points.length ? ratingChart(points, name, width) : emptyMessage("No rating yet."),
    );
  }

  function rivalTile(label, matchup) {
    const player = participants.players[matchup.opponent];
    const item = tile(label);
    const row = element("div", "rival");
    const link = element("a", "rival__name", player.name);
    link.href = playerHash(player.id, ALL_MODES);
    row.append(UI.avatar(matchup.opponent, 36), link, element("strong", "rival__record", `${matchup.wins}–${matchup.losses}`));
    item.append(row);
    return item;
  }

  function renderRivals(playerIndex) {
    const candidates = computeHeadToHead(playerIndex, ALL_MODES)
      .filter((matchup) => matchup.wins + matchup.losses >= RIVAL_MIN_DECISIVE)
      .map((matchup) => ({ ...matchup, rate: matchup.wins / (matchup.wins + matchup.losses) }));
    const pick = (list, order) => list.sort((left, right) => order * (left.rate - right.rate) || right.meetings - left.meetings)[0];
    const nemesis = pick(candidates.filter((matchup) => matchup.rate < 0.5), 1);
    const victim = pick(candidates.filter((matchup) => matchup.rate > 0.5), -1);
    const tiles = [];
    if (nemesis) {
      tiles.push(rivalTile("Nemesis", nemesis));
    }
    if (victim) {
      tiles.push(rivalTile("Favourite victim", victim));
    }
    const host = document.querySelector("#player-rivals");
    host.hidden = !tiles.length;
    host.replaceChildren(...tiles);
  }

  function render(playerIndex) {
    if (renderedPlayer !== playerIndex) {
      renderedPlayer = playerIndex;
      renderHeading(playerIndex);
      renderBadges(playerIndex);
      renderPbs(playerIndex);
      renderRating(playerIndex);
      renderRivals(playerIndex);
    }
  }

  return { render };
})();
