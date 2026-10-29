const AVATAR_BASE_URL = "https://racetime.gg/media/";
const COMMENTS_URL = "data/comments.json";
const DIALOG_DEFAULT_MS = 3000;
const SPEECH_AVATAR_SIZES = { hero: 56, compact: 28 };
const TAG_KINDS = new Set(["pb", "record", "first_win", "photo", "upset", "hardest", "milestone", "badge", "new"]);
// Size, radius and border must match .ui-badge__tile in ui.css.
const BADGE_TILE_SIZE = 60;
const BADGE_TILE_RADIUS = 12;
const BADGE_TILE_BORDER = 3;
const BADGE_IMAGE_SIZE = 46;

const UI = (() => {
  let commentsPromise = null;
  let followListenerBound = false;
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

  function avatarUrl(playerIndex) {
    const player = participants.players[playerIndex];
    return player && player.a ? AVATAR_BASE_URL + encodeURIComponent(player.a) : null;
  }

  function monogram(name, size) {
    const tile = element("span", "ui-avatar__monogram", (name.trim().charAt(0) || "?").toUpperCase());
    tile.style.fontSize = `${Math.round(size * 0.55)}px`;
    return tile;
  }

  function avatar(playerIndex, size) {
    const player = participants.players[playerIndex];
    const name = player ? player.name : "";
    const wrapper = element("span", "ui-avatar");
    wrapper.style.width = `${size}px`;
    wrapper.style.height = `${size}px`;
    const url = avatarUrl(playerIndex);
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
    image.referrerPolicy = "no-referrer";
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

  function tag(kind, label, detail = null) {
    const safeKind = TAG_KINDS.has(kind) ? kind : "badge";
    const pill = element("span", `ui-tag ui-tag--${safeKind}`);
    pill.appendChild(element("span", "ui-tag__dot"));
    pill.appendChild(element("span", "ui-tag__label", label));
    if (detail) {
      bindTooltip(pill, detail);
    }
    return pill;
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
    document.addEventListener("racetimevibes:following", () => {
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
      document.dispatchEvent(new CustomEvent("racetimevibes:palindrome", { detail: { seconds } }));
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

  // Share of all racers holding the badge, to two significant digits.
  function badgeShare(badgeId) {
    const share = (badgeHolderCounts.get(badgeId) ?? 0) / participants.players.length;
    return `Held by ${shareFormatter.format(share)} of racers`;
  }

  function badgeTooltip(definition, earnedRaceIndex, progress) {
    const description = definition.description || "";
    let status = "";
    if (earnedRaceIndex !== null && earnedRaceIndex !== undefined) {
      const date = participants.races[earnedRaceIndex].d;
      status = `Earned ${formatRaceDate(date)}`;
    } else if (definition.target && progress !== null && progress !== undefined) {
      status = `${progress} / ${definition.target}`;
    }
    return [description, status, badgeShare(definition.id)].filter(Boolean).join(" · ");
  }

  function badge(definition, { earnedRaceIndex = null, progress = null } = {}) {
    const earned = earnedRaceIndex !== null && earnedRaceIndex !== undefined;
    const root = element("div", `ui-badge ui-badge--${earned ? "earned" : "locked"}`);
    const tile = element("span", "ui-badge__tile");
    if (!earned && definition.target && progress) {
      tile.appendChild(progressFrame(definition, progress));
    }
    tile.appendChild(badgeArt(definition, earned));
    root.appendChild(tile);
    root.appendChild(element("span", "ui-badge__label", definition.label));
    const tooltipText = badgeTooltip(definition, earnedRaceIndex, progress);
    if (tooltipText) {
      bindTooltip(root, tooltipText);
    }
    return root;
  }

  function dialogBox(text, { modal = false, onClose = null, duration = DIALOG_DEFAULT_MS } = {}) {
    if (openDialog) {
      openDialog.close();
    }
    const previousFocus = document.activeElement;
    const box = element("div", "ui-dialog", text);
    box.tabIndex = -1;
    box.setAttribute("role", modal ? "dialog" : "status");
    if (modal) {
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-label", text);
    }
    const backdrop = modal ? element("div", "ui-dialog-backdrop") : null;
    let timer = null;
    let closed = false;

    function close() {
      if (closed) {
        return;
      }
      closed = true;
      clearTimeout(timer);
      document.removeEventListener("keydown", onKeydown);
      box.remove();
      if (backdrop) {
        backdrop.remove();
      }
      if (openDialog === handle) {
        openDialog = null;
      }
      if (modal && previousFocus && previousFocus.isConnected && typeof previousFocus.focus === "function") {
        previousFocus.focus();
      }
      if (onClose) {
        onClose();
      }
    }

    function onKeydown(event) {
      if (event.key === "Escape") {
        close();
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
      document.addEventListener("keydown", onKeydown);
      box.focus();
    } else {
      timer = setTimeout(close, duration);
    }
    return handle;
  }

  function comments() {
    if (!commentsPromise) {
      commentsPromise = fetch(COMMENTS_URL, { cache: "no-store" })
        .then((response) => {
          if (!response.ok) {
            throw new Error(`${COMMENTS_URL} returned ${response.status}`);
          }
          return response.json();
        })
        .catch((error) => {
          console.error("Could not load comments.", error);
          return null;
        });
    }
    return commentsPromise;
  }

  function relativeDay(isoDate) {
    const toUtcDay = (iso) => Date.parse(`${iso}T00:00:00Z`);
    const days = Math.round((toUtcDay(todayInZone()) - toUtcDay(isoDate)) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    return `${days} days ago`;
  }

  return { avatarUrl, avatar, speech, tag, followStar, isPalindromeTime, time, badge, badgeShare, dialogBox, comments, relativeDay };
})();
