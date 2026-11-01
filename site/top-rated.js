const TopRated = (() => {
  const AVATAR_SIZE = 36;
  const MAX_AVATARS = 3;
  const MAX_NAMED = 2;

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

  function crown(className) {
    const icon = svgElement("svg", {
      class: className,
      viewBox: "0 0 24 16",
      "aria-hidden": "true",
      focusable: "false",
    });
    icon.append(svgElement("path", { d: "M2 14 3.5 4.5 8.5 9 12 2l3.5 7 5-4.5L22 14Z" }));
    return icon;
  }

  function leadersFor(modeId) {
    return [...ratingRanks(modeId)]
      .filter(([, rank]) => rank === 1)
      .map(([playerIndex]) => playerIndex)
      .sort((a, b) => a - b);
  }

  function namesLine(leaders, modeId) {
    const names = element("span", "top-rated__names");
    leaders.slice(0, MAX_NAMED).forEach((playerIndex, position) => {
      const player = participants.players[playerIndex];
      if (position > 0) {
        names.append(document.createTextNode(" & "));
      }
      const link = element("a", "racer-link", player.name);
      link.href = playerHash(player.id, modeId);
      names.append(link);
    });
    const rest = leaders.slice(MAX_NAMED);
    if (rest.length > 0) {
      const more = element("span", "top-rated__more", ` +${rest.length}`);
      more.title = rest.map((playerIndex) => participants.players[playerIndex].name).join(", ");
      names.append(more);
    }
    return names;
  }

  function tile(modeId, leaders) {
    const overall = modeId === ALL_MODES;
    const item = element("li", overall ? "top-rated__tile top-rated__tile--overall" : "top-rated__tile");
    item.style.setProperty("--tile-color", overall ? "var(--gold)" : modeColor(modeId));

    const avatars = element("span", "top-rated__avatars");
    leaders.slice(0, MAX_AVATARS).forEach((playerIndex) => {
      avatars.append(UI.avatar(playerIndex, AVATAR_SIZE));
    });
    avatars.append(crown("top-rated__crown"));

    const text = element("div", "top-rated__text");
    text.append(
      element("span", "top-rated__label", overall ? "Overall" : modeLabel(modeId)),
      namesLine(leaders, modeId),
      element("span", "top-rated__rating", String(ratingFor(participants.players[leaders[0]], modeId).rating)),
    );

    item.append(avatars, text);
    return item;
  }

  function init() {
    if (started) {
      return;
    }
    started = true;

    const tiles = [];
    for (const modeId of ratingCategories()) {
      const leaders = leadersFor(modeId);
      if (leaders.length > 0) {
        tiles.push(tile(modeId, leaders));
      }
    }
    if (tiles.length === 0) {
      return;
    }

    document.getElementById("top-rated-list").replaceChildren(...tiles);
    document.getElementById("top-rated-title").prepend(crown("top-rated__crown top-rated__crown--title"));
    document.getElementById("top-rated").hidden = false;
  }

  return { init };
})();
