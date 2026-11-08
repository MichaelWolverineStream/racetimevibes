const Upcoming = (() => {
  const SHOWN = 5;
  const REFRESH_MS = 60_000;
  const whenFormatter = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  });
  let started = false;

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

  // "in 25 min", "in 3 h 20 min", "in 2 d 4 h": rounded up, so a slot never reads as started early.
  function startsIn(seconds) {
    const minutes = Math.max(1, Math.ceil(seconds / 60));
    if (minutes < 60) {
      return `in ${minutes} min`;
    }
    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
      return minutes % 60 ? `in ${hours} h ${minutes % 60} min` : `in ${hours} h`;
    }
    return hours % 24 ? `in ${Math.floor(hours / 24)} d ${hours % 24} h` : `in ${Math.floor(hours / 24)} d`;
  }

  // Only a champion season is "the Season of the …"; any other reads as its label.
  function title(season) {
    const name = seasonName(season);
    return name?.kind === "champion"
      ? `Upcoming races in the Season of the ${name.text}`
      : `Upcoming races in ${seasonLabel(season)}`;
  }

  // The slots still to start, all from the season of the next one.
  function nextSlots(now) {
    const ahead = participants.upcoming.filter(([time]) => time * 1000 > now);
    if (ahead.length === 0 || ahead[0][3] === null) {
      return { season: null, slots: [] };
    }
    const season = ahead[0][3];
    return { season, slots: ahead.filter((slot) => slot[3] === season).slice(0, SHOWN) };
  }

  function slotItem([time, modeName, modeIndex], now) {
    const start = new Date(time * 1000);
    const item = element("li", "upcoming__slot");
    const when = element("time", "upcoming__when", whenFormatter.format(start));
    when.dateTime = start.toISOString();
    item.append(when, element("span", "upcoming__in", startsIn(time - now / 1000)));
    const mode = element("span", "upcoming__mode");
    const dot = modeIndex === null ? element("span", "ui-mode-dot ui-mode-dot--none") : UI.modeDot(participants.modes[modeIndex]);
    dot.setAttribute("aria-hidden", "true");
    mode.append(dot, element("span", "upcoming__mode-name", modeName));
    item.appendChild(mode);
    return item;
  }

  function render() {
    const now = Date.now();
    const { season, slots } = nextSlots(now);
    const section = document.getElementById("upcoming");
    section.hidden = slots.length === 0;
    if (slots.length === 0) {
      return;
    }
    document.getElementById("upcoming-title").textContent = title(season);
    document.getElementById("upcoming-list").replaceChildren(...slots.map((slot) => slotItem(slot, now)));
  }

  // Redrawn every minute, so the countdowns tick and a slot drops off once it starts.
  function init() {
    if (started) {
      return;
    }
    started = true;
    render();
    window.setInterval(render, REFRESH_MS);
  }

  return { init };
})();
