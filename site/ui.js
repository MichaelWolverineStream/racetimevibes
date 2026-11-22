// Mirrored from racetime.gg by scripts/avatar_mirror.py, so canvases can draw them.
const AVATAR_BASE_URL = "avatars/";
// A racetime.gg avatar `<hash>.webp` is 128 px; the face-off's 256 px copy is `<hash>-256.webp`.
// Must match FACEOFF_SUFFIX in avatar_mirror.py.
const FACEOFF_AVATAR_SUFFIX = "-256";
// A Pokémon avatar is one 256 px sprite, the face-off's included. Must match AVATAR_PREFIX in
// pokemon_avatars.py.
const POKEMON_AVATAR_PREFIX = "pokemon-";
const COMMENTS_URL = "data/comments.json";
const COMMENTS_SCHEMA_VERSION = 2;
const PALINDROME_EVENT = "racetimevibes:palindrome";
const DIALOG_DEFAULT_MS = 3000;
const SPEECH_AVATAR_SIZES = { hero: 56, compact: 28 };
const TAG_KINDS = new Set(["pb", "preset_pb", "record", "preset_record", "first_win", "photo", "upset", "hardest", "milestone", "badge"]);
// A badge's rarity by the share of racers holding it, rarest first: the first limit the share is
// at or under names it, and a share above every limit is common. Names must match the
// .ui-tag--rare and .ui-tag--legendary rules in ui.css.
const BADGE_RARITIES = [
  ["legendary", 0.02],
  ["rare", 0.1],
];
// The look of an earned repeatable badge, by how many times it was earned: the highest floor at or
// under the count names its tier. Must match the .ui-badge--repeat-N rules in ui.css.
const BADGE_REPEAT_TIERS = [10, 5, 2, 1];
// Size, radius and border must match .ui-badge__tile in ui.css.
const BADGE_TILE_SIZE = 60;
const BADGE_TILE_RADIUS = 12;
const BADGE_TILE_BORDER = 3;
const BADGE_IMAGE_SIZE = 46;
// Must match the .ui-avatar__monogram--tint-N rules in ui.css.
const MONOGRAM_TINTS = 6;

const UI = (() => {
  let commentsPromise = null;
  let followListenerBound = false;
  let meListenerBound = false;
  let openDialog = null;
  const shareFormatter = new Intl.NumberFormat("en-US", { style: "percent", maximumSignificantDigits: 2 });

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

  // The published file everywhere but the face-off, which takes a racetime.gg avatar's larger copy.
  function avatarFile(name, faceoff) {
    if (!faceoff || name.startsWith(POKEMON_AVATAR_PREFIX)) {
      return name;
    }
    const dot = name.lastIndexOf(".");
    return name.slice(0, dot) + FACEOFF_AVATAR_SUFFIX + name.slice(dot);
  }

  function avatarUrl(playerIndex, { faceoff = false } = {}) {
    const player = participants.players[playerIndex];
    return player && player.a ? AVATAR_BASE_URL + encodeURIComponent(avatarFile(player.a, faceoff)) : null;
  }

  // The same name always gets the same tint, so neighbouring monograms stay distinct.
  function monogramTint(name) {
    let hash = 0;
    for (const character of name) {
      hash = (hash * 31 + character.codePointAt(0)) >>> 0;
    }
    return (hash % MONOGRAM_TINTS) + 1;
  }

  // The first code point, so a name that opens with an emoji keeps it whole.
  function monogramLetter(name) {
    return (Array.from(name.trim())[0] || "?").toUpperCase();
  }

  function monogram(name, size) {
    const className = `ui-avatar__monogram ui-avatar__monogram--tint-${monogramTint(name)}`;
    const tile = element("span", className, monogramLetter(name));
    tile.style.fontSize = `${Math.round(size * 0.55)}px`;
    return tile;
  }

  function avatar(playerIndex, size, { faceoff = false } = {}) {
    const player = participants.players[playerIndex];
    const name = player ? player.name : "";
    const wrapper = element("span", "ui-avatar");
    wrapper.style.width = `${size}px`;
    wrapper.style.height = `${size}px`;
    const url = avatarUrl(playerIndex, { faceoff });
    if (!url) {
      wrapper.appendChild(monogram(name, size));
      return wrapper;
    }
    const image = document.createElement("img");
    image.className = "ui-avatar__image";
    image.width = size;
    image.height = size;
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    image.addEventListener("error", () => image.replaceWith(monogram(name, size)));
    image.src = url;
    wrapper.appendChild(image);
    return wrapper;
  }

  function attributionPart(part, player) {
    const href = part.href || (player && part.text === player.name ? playerHash(player.id, ALL_MODES) : null);
    if (!href) {
      return element("span", null, part.text);
    }
    const link = element("a", "ui-speech__link", part.text);
    link.href = href;
    if (/^https?:/.test(href)) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    if (part.tooltip) {
      bindTooltip(link, part.tooltip);
    }
    return link;
  }

  function speech(text, { playerIndex = null, context = [], size = "compact" } = {}) {
    const player = playerIndex === null ? null : participants.players[playerIndex];
    const wrapper = element("div", `ui-speech ui-speech--${size}`);
    if (player) {
      wrapper.appendChild(avatar(playerIndex, SPEECH_AVATAR_SIZES[size] ?? SPEECH_AVATAR_SIZES.compact));
    }
    const body = element("div", "ui-speech__body");
    body.appendChild(element("p", "ui-speech__bubble", text));
    if (context.length) {
      const attribution = element("p", "ui-speech__attribution");
      context.forEach((part, index) => {
        if (index > 0) {
          attribution.appendChild(document.createTextNode(" · "));
        }
        attribution.appendChild(attributionPart(part, player));
      });
      body.appendChild(attribution);
    }
    wrapper.appendChild(body);
    return wrapper;
  }

  // Rarity shows only on a badge tag: rare and legendary badges get a fill, and legendary ones shine (see ui.css).
  function tag(kind, label, detail = null, rarity = "common") {
    const safeKind = TAG_KINDS.has(kind) ? kind : "badge";
    const shines = safeKind === "badge" && BADGE_RARITIES.some(([name]) => name === rarity);
    const pill = element("span", `ui-tag ui-tag--${safeKind}${shines ? ` ui-tag--${rarity}` : ""}`);
    pill.appendChild(element("span", "ui-tag__dot"));
    pill.appendChild(element("span", "ui-tag__label", label));
    if (detail) {
      bindTooltip(pill, detail);
    }
    return pill;
  }

  function modeDot(modeName) {
    const dot = element("span", "ui-mode-dot");
    dot.style.background = modeColor(modeName);
    return dot;
  }

  // The rating leader's crown, on the top-rated banner and the racer page's rating strip. Each caller's
  // class sizes, places and colours it.
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

  function syncFollowStar(button) {
    const player = participants.players[Number(button.dataset.player)];
    const following = Following.has(player.id);
    button.textContent = following ? "★" : "☆";
    button.setAttribute("aria-pressed", String(following));
    button.setAttribute("aria-label", `${following ? "Unfollow" : "Follow"} ${player.name}`);
  }

  // One listener for every star on the page; per-button listeners would pile up as tables re-render.
  function watchFollowing() {
    if (followListenerBound) {
      return;
    }
    followListenerBound = true;
    document.addEventListener(FOLLOWING_EVENT, () => {
      document.querySelectorAll("button.follow-star[data-player]").forEach(syncFollowStar);
    });
  }

  function followStar(playerIndex) {
    const player = participants.players[playerIndex];
    const button = element("button", "follow-star");
    button.type = "button";
    button.dataset.player = String(playerIndex);
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      Following.toggle(player.id);
    });
    watchFollowing();
    syncFollowStar(button);
    return button;
  }

  // "This is me" marks: a node showing racers carries their indexes in data-racers, and data-me
  // while the viewer's own racer is among them. Marks follow a change by this sync, never by a re-render.
  function syncMark(node, me) {
    node.toggleAttribute("data-me", me !== null && node.dataset.racers.split(" ").includes(String(me)));
  }

  // Re-syncs a subtree; cached nodes that were off the page when the racer changed call this when re-attached.
  function syncMarks(root = document) {
    const me = meIndex();
    root.querySelectorAll("[data-racers]").forEach((node) => syncMark(node, me));
  }

  // One listener for every mark on the page, like the follow stars'.
  function watchMe() {
    if (meListenerBound) {
      return;
    }
    meListenerBound = true;
    document.addEventListener(ME_EVENT, () => syncMarks());
  }

  function markRacers(node, playerIndexes) {
    node.dataset.racers = playerIndexes.join(" ");
    syncMark(node, meIndex());
    watchMe();
    return node;
  }

  // The locked counter badge the racer is closest to finishing; table order breaks ties.
  function nextBadge(player) {
    let best = null;
    participants.badges.forEach((definition) => {
      const progress = player.progress[definition.id];
      if (!progress || !definition.target) {
        return;
      }
      const share = progress / definition.target;
      if (!best || share > best.share) {
        best = { definition, progress, share };
      }
    });
    return best;
  }


  // H:MM:SS digits with the hour unpadded and the colons removed, read the same both ways.
  function isPalindromeTime(seconds) {
    if (seconds === null || seconds === undefined) {
      return false;
    }
    const total = Math.round(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const rest = total % 60;
    const digits = `${hours}${String(minutes).padStart(2, "0")}${String(rest).padStart(2, "0")}`;
    return digits === [...digits].reverse().join("");
  }

  function time(seconds) {
    const span = element("span", "ui-time", formatDuration(seconds));
    if (!isPalindromeTime(seconds)) {
      return span;
    }
    const spark = element("button", "palindrome-spark", "✦");
    spark.type = "button";
    spark.setAttribute("aria-label", "Palindrome time");
    spark.addEventListener("animationend", () => spark.classList.remove("palindrome-spark--twinkle"));
    spark.addEventListener("click", (event) => {
      event.stopPropagation();
      spark.classList.remove("palindrome-spark--twinkle");
      void spark.offsetWidth;
      spark.classList.add("palindrome-spark--twinkle");
      document.dispatchEvent(new CustomEvent(PALINDROME_EVENT, { detail: { seconds } }));
    });
    span.appendChild(spark);
    return span;
  }

  // A rounded rectangle on the tile border's midline, starting at top centre and running clockwise.
  function progressFrame(definition, progress) {
    const fraction = Math.min(1, Math.max(0, progress / definition.target));
    const inset = BADGE_TILE_BORDER / 2;
    const radius = BADGE_TILE_RADIUS - inset;
    const low = inset;
    const high = BADGE_TILE_SIZE - inset;
    const middle = BADGE_TILE_SIZE / 2;
    const path = [
      `M ${middle} ${low}`,
      `H ${high - radius}`, `A ${radius} ${radius} 0 0 1 ${high} ${low + radius}`,
      `V ${high - radius}`, `A ${radius} ${radius} 0 0 1 ${high - radius} ${high}`,
      `H ${low + radius}`, `A ${radius} ${radius} 0 0 1 ${low} ${high - radius}`,
      `V ${low + radius}`, `A ${radius} ${radius} 0 0 1 ${low + radius} ${low}`,
      "Z",
    ].join(" ");
    const svg = svgElement("svg", { viewBox: `0 0 ${BADGE_TILE_SIZE} ${BADGE_TILE_SIZE}`, width: BADGE_TILE_SIZE, height: BADGE_TILE_SIZE, "aria-hidden": "true", focusable: "false" });
    svg.appendChild(svgElement("path", { class: "ui-badge__progress", d: path, pathLength: 1, fill: "none", "stroke-width": BADGE_TILE_BORDER, "stroke-linecap": "butt", "stroke-dasharray": `${fraction} 1` }));
    return svg;
  }

  function badgeImage(src) {
    const image = element("img", "ui-badge__image");
    image.src = src;
    image.alt = "";
    image.width = BADGE_IMAGE_SIZE;
    image.height = BADGE_IMAGE_SIZE;
    image.loading = "lazy";
    image.decoding = "async";
    image.draggable = false;
    return image;
  }

  function badgeArt(definition, earned) {
    if (!earned) {
      return badgeImage(definition.still);
    }
    // Animated art falls back to its still frame for reduced-motion viewers.
    const picture = element("picture");
    const still = element("source");
    still.srcset = definition.still;
    still.media = "(prefers-reduced-motion: reduce)";
    picture.append(still, badgeImage(definition.image));
    return picture;
  }

  // Share of all racers holding the badge, from 0 to 1.
  function holderShare(badgeId) {
    return (badgeHolderCounts.get(badgeId) ?? 0) / participants.players.length;
  }

  // The holder share as tooltip text, to two significant digits.
  function badgeShare(badgeId) {
    return `Held by ${shareFormatter.format(holderShare(badgeId))} of racers`;
  }

  // "legendary", "rare" or "common", from the exact holder share (see BADGE_RARITIES).
  function badgeRarity(badgeId) {
    const share = holderShare(badgeId);
    const rarity = BADGE_RARITIES.find(([, limit]) => share <= limit);
    return rarity ? rarity[0] : "common";
  }

  // "once" or "7 times", for a repeatable badge's count.
  function timesText(count) {
    return count === 1 ? "once" : `${count} times`;
  }

  // The repeat tier class of an earned repeatable badge earned `count` times (see BADGE_REPEAT_TIERS).
  function repeatClass(count) {
    const floor = BADGE_REPEAT_TIERS.find((tier) => count >= tier) ?? 1;
    return `ui-badge--repeat-${floor}`;
  }

  // `note` is the text a badge given by hand may carry (the racer's badge_notes); it follows the date.
  // A repeatable badge adds how many times it was earned, or "Repeatable" while it is locked.
  function badgeTooltip(definition, earnedRaceIndex, progress, note, count) {
    const description = definition.description || "";
    const earned = earnedRaceIndex !== null && earnedRaceIndex !== undefined;
    let status = "";
    let repeats = "";
    if (earned) {
      const date = formatRaceDate(participants.races[earnedRaceIndex].d);
      const many = definition.repeatable && count > 1;
      status = many ? `First earned ${date}` : `Earned ${date}`;
      if (definition.repeatable) {
        repeats = many ? timesText(count) : "Once so far";
      }
    } else {
      if (definition.target && progress !== null && progress !== undefined) {
        status = `${progress} / ${definition.target}`;
      }
      if (definition.repeatable) {
        repeats = "Repeatable";
      }
    }
    return [description, status, repeats, note, badgeShare(definition.id)].filter(Boolean).join(" · ");
  }

  // A ×10 tile's glint: light runs across it and its diamond twinkles, once on arrival and again on
  // hover or keyboard focus. Restarting means removing the class and forcing a reflow before adding it.
  function bindGlint(root, tile) {
    const glint = () => {
      root.classList.remove("ui-badge--glint");
      void tile.offsetWidth;
      root.classList.add("ui-badge--glint");
    };
    tile.addEventListener("animationend", (event) => {
      if (event.animationName === "ui-badge-glint") {
        root.classList.remove("ui-badge--glint");
      }
    });
    root.addEventListener("pointerenter", (event) => {
      if (!isTouchPointer(event)) {
        glint();
      }
    });
    root.addEventListener("focus", () => {
      if (root.matches(":focus-visible")) {
        glint();
      }
    });
    root.classList.add("ui-badge--glint");
  }

  // A compact tile's art: the still frame, animated only while a mouse hovers the tile or it has
  // keyboard focus, so a row of them stays calm and a tap never starts it. Reduced-motion
  // viewers always see the still.
  function compactArt(definition, root) {
    const image = badgeImage(definition.still);
    const play = (event) => {
      const wanted = event.type === "focus" ? root.matches(":focus-visible") : !isTouchPointer(event);
      if (wanted && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        image.src = definition.image;
      }
    };
    const rest = () => {
      image.src = definition.still;
    };
    root.addEventListener("pointerenter", play);
    root.addEventListener("pointerleave", rest);
    root.addEventListener("focus", play);
    root.addEventListener("blur", rest);
    return image;
  }

  // `compact` draws an earned badge as a small tile with no label: its name leads the tooltip
  // and names the button instead. `count` is how many times the racer earned a repeatable badge
  // (their badge_counts), and `holder` is the racer's player index, which the holders panel names.
  function badge(definition, { earnedRaceIndex = null, progress = null, note = null, compact = false, count = null, holder = null } = {}) {
    const earned = earnedRaceIndex !== null && earnedRaceIndex !== undefined;
    const repeats = earned && definition.repeatable ? (count ?? 1) : null;
    const classes = ["ui-badge", `ui-badge--${earned ? "earned" : "locked"}`];
    if (compact) {
      classes.push("ui-badge--compact");
    }
    if (repeats !== null) {
      classes.push(repeatClass(repeats));
    }
    const root = element("button", classes.join(" "));
    root.type = "button";
    root.setAttribute("aria-haspopup", "dialog");
    const tile = element("span", "ui-badge__tile");
    if (!earned && !compact && definition.target && progress) {
      tile.appendChild(progressFrame(definition, progress));
    }
    tile.appendChild(compact && earned ? compactArt(definition, root) : badgeArt(definition, earned));
    root.appendChild(tile);
    let tooltipText = badgeTooltip(definition, earnedRaceIndex, progress, note, repeats);
    if (compact) {
      tooltipText = [definition.label, tooltipText].filter(Boolean).join(" · ");
    } else {
      root.appendChild(element("span", "ui-badge__label", definition.label));
    }
    const many = repeats !== null && repeats > 1;
    if (compact || many) {
      root.setAttribute("aria-label", many ? `${definition.label}, ${timesText(repeats)}` : definition.label);
    }
    if (tooltipText) {
      bindTooltip(root, tooltipText);
    }
    if (repeats !== null && repeats >= BADGE_REPEAT_TIERS[0]) {
      bindGlint(root, tile);
    }
    root.addEventListener("click", () => badgePanel(definition, { holder }));
    return root;
  }

  // Makes the page behind a modal inert and closes it on Escape. The returned release()
  // must run after the modal's nodes are gone: it lifts inert first, because focus()
  // into an inert page silently does nothing.
  function holdModal(close) {
    const previousFocus = document.activeElement;
    const inertHosts = [document.querySelector("main"), document.querySelector("footer")].filter(Boolean);

    function onKeydown(event) {
      if (event.key === "Escape") {
        close();
      }
    }

    inertHosts.forEach((host) => {
      host.inert = true;
    });
    document.addEventListener("keydown", onKeydown);

    return function release() {
      document.removeEventListener("keydown", onKeydown);
      inertHosts.forEach((host) => {
        host.inert = false;
      });
      if (previousFocus && previousFocus.isConnected && typeof previousFocus.focus === "function") {
        previousFocus.focus();
      }
    };
  }

  // A centred box over a scrim, holding only its close button; the caller appends the content.
  // The box keeps the badge panel's class names, because the badge panel was its first user.
  function modalPanel({ className = "", labelledBy, onClose = null }) {
    if (openDialog) {
      openDialog.close();
    }
    const backdrop = element("div", "ui-badge-panel-backdrop");
    const panel = element("div", ["ui-badge-panel", className].filter(Boolean).join(" "));
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-labelledby", labelledBy);
    let closed = false;

    // onClose runs last, once openDialog is cleared and focus is restored.
    function close() {
      if (closed) {
        return;
      }
      closed = true;
      // A tooltip bound inside the panel would outlive its element, which is removed before mouseleave can fire.
      hideTooltip();
      panel.remove();
      backdrop.remove();
      if (openDialog === handle) {
        openDialog = null;
      }
      release();
      if (onClose) {
        onClose();
      }
    }

    const closeButton = element("button", "ui-badge-panel__close", "×");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Close");
    closeButton.addEventListener("click", close);

    const handle = { element: panel, close };
    backdrop.addEventListener("click", close);
    panel.append(closeButton);
    document.body.append(backdrop, panel);
    openDialog = handle;
    const release = holdModal(close);
    hideTooltip();
    closeButton.focus();
    return { panel, close };
  }

  // `holder` is the player index of the racer whose tile opened the panel: for a repeatable badge
  // they hold, a line under the count says how many times they earned it.
  function badgePanel(definition, { holder = null } = {}) {
    const titleId = `ui-badge-panel-title-${definition.id}`;
    const { panel, close } = modalPanel({ labelledBy: titleId });
    const timesEarned = (player) => player.badge_counts[definition.id] ?? 1;

    const holders = [];
    participants.players.forEach((player, playerIndex) => {
      if (player.badges[definition.id] !== undefined) {
        holders.push({ player, playerIndex, raceIndex: player.badges[definition.id] });
      }
    });
    holders.sort((a, b) => a.raceIndex - b.raceIndex || a.playerIndex - b.playerIndex);

    const tile = element("span", "ui-badge__tile");
    tile.appendChild(badgeArt(definition, true));
    const artClasses = ["ui-badge", "ui-badge--earned", "ui-badge-panel__art"];
    if (definition.repeatable) {
      artClasses.push(repeatClass(1));
    }
    const art = element("span", artClasses.join(" "));
    art.appendChild(tile);

    const title = element("h2", "ui-badge-panel__title", definition.label);
    title.id = titleId;
    const share = shareFormatter.format(holders.length / participants.players.length);
    const countParts = [`${holders.length} ${holders.length === 1 ? "racer" : "racers"}`, share];
    if (definition.repeatable && holders.length) {
      const total = holders.reduce((sum, { player }) => sum + timesEarned(player), 0);
      countParts.push(total === 1 ? "earned once" : `earned ${total} times in all`);
    }
    const text = element("div", "ui-badge-panel__text");
    text.append(title, element("p", "ui-badge-panel__description", definition.description || ""), element("p", "ui-badge-panel__count", countParts.join(" · ")));
    const opener = holder === null ? null : participants.players[holder];
    if (definition.repeatable && opener && opener.badges[definition.id] !== undefined) {
      text.append(element("p", "ui-badge-panel__count", `${opener.name} · ${timesText(timesEarned(opener))}`));
    }
    const header = element("div", "ui-badge-panel__header");
    header.append(art, text);

    let body;
    if (holders.length) {
      body = element("div", "ui-badge-panel__holders");
      holders.forEach(({ player, playerIndex }) => {
        const label = definition.repeatable ? `${player.name} · ${timesText(timesEarned(player))}` : player.name;
        const link = markRacers(element("a", "ui-badge-panel__holder"), [playerIndex]);
        link.href = playerHash(player.id, ALL_MODES);
        link.setAttribute("aria-label", label);
        link.appendChild(avatar(playerIndex, 40));
        link.addEventListener("click", close);
        bindTooltip(link, label);
        body.appendChild(link);
      });
    } else {
      body = element("p", "ui-badge-panel__empty", "Nobody has earned this badge yet.");
    }

    panel.append(header, body);
    return { element: panel, close };
  }

  function dialogBox(text, { modal = false, onClose = null, duration = DIALOG_DEFAULT_MS } = {}) {
    if (openDialog) {
      openDialog.close();
    }
    const box = element("div", "ui-dialog", text);
    box.tabIndex = -1;
    box.setAttribute("role", modal ? "dialog" : "status");
    if (modal) {
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-label", text);
    }
    const backdrop = modal ? element("div", "ui-dialog-backdrop") : null;
    let timer = null;
    let release = null;
    let closed = false;

    // onClose runs last, once openDialog is cleared, so a dialog it opens is not closed straight away.
    function close() {
      if (closed) {
        return;
      }
      closed = true;
      clearTimeout(timer);
      box.remove();
      if (backdrop) {
        backdrop.remove();
      }
      if (openDialog === handle) {
        openDialog = null;
      }
      if (release) {
        release();
      }
      if (onClose) {
        onClose();
      }
    }

    const handle = { element: box, close };
    box.addEventListener("click", close);
    if (backdrop) {
      backdrop.addEventListener("click", close);
      document.body.appendChild(backdrop);
    }
    document.body.appendChild(box);
    openDialog = handle;
    if (modal) {
      release = holdModal(close);
      box.focus();
    } else {
      timer = setTimeout(close, duration);
    }
    return handle;
  }

  function comments() {
    if (!commentsPromise) {
      commentsPromise = fetch(COMMENTS_URL, { cache: "no-cache" })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`${COMMENTS_URL} returned ${response.status}`);
          }
          return response.json();
        })
        .then((payload) => {
          if (
            !payload ||
            payload.schema_version !== COMMENTS_SCHEMA_VERSION ||
            !payload.races ||
            typeof payload.races !== "object" ||
            Array.isArray(payload.races)
          ) {
            throw new Error(`Unexpected ${COMMENTS_URL} schema version.`);
          }
          return payload;
        })
        .catch((error) => {
          console.error("Could not load comments.", error);
          return null;
        });
    }
    return commentsPromise;
  }

  function chestPace(payload, raceName, userId, seconds) {
    const entries = payload && payload.races ? payload.races[raceName] : null;
    const match = (entries || []).find(([id]) => id === userId);
    if (!match || match[2] === null || match[2] === undefined || !seconds) {
      return null;
    }
    return { chests: match[2], perHour: Math.round((match[2] * 3600) / seconds) };
  }

  function relativeDay(isoDate) {
    const toUtcDay = (iso) => Date.parse(`${iso}T00:00:00Z`);
    const days = Math.round((toUtcDay(todayInZone()) - toUtcDay(isoDate)) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    return `${days} days ago`;
  }

  return { avatarUrl, avatar, monogramLetter, monogramTint, speech, tag, modeDot, crown, followStar, markRacers, syncMarks, nextBadge, time, badge, badgePanel, badgeShare, badgeRarity, modalPanel, dialogBox, comments, chestPace, relativeDay };
})();
