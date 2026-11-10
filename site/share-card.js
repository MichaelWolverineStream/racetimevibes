const ShareCard = (() => {
  // The card is laid out in CSS pixels; SCALE multiplies the exported bitmap for a sharper image.
  const WIDTH = 600;
  const HEIGHT = 300;
  const SCALE = 1;
  const PAD = 18;
  const RADIUS = 12;
  const RAIL = 6;
  const AVATAR_SIZE = 72;
  const STAT_WIDTH = 74;
  const SPARK = { left: 330, right: WIDTH - PAD, top: 116, bottom: 160 };
  const BADGE_LEFT = 88;
  const BADGE_TOP = 174;
  const BADGE_TILE = 28;
  const BADGE_GAP = 4;
  const NEMESIS_AVATAR = 18;
  const BRAND = "Climbers";
  const CATEGORY_MARK = "assets/alttpr-ladder.png";
  const TITLE_ID = "share-card-title";
  // Fills for UI.monogramTint's 1-6; must match the .ui-avatar__monogram--tint-N rules in ui.css.
  const MONOGRAM_FILLS = ["--accent", "--teal", "--teal-deep", "--gold-ink", "--bronze-ink", "--silver-ink"];
  const monthFormatter = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

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

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function palette() {
    return {
      panel: token("--panel"),
      ink: token("--ink"),
      muted: token("--muted"),
      accent: token("--accent"),
      gold: token("--gold"),
      goldInk: token("--gold-ink"),
      red: token("--red"),
      line: token("--line"),
      grid: token("--grid"),
      seriesMuted: token("--series-muted"),
      positive: token("--positive"),
      negative: token("--negative"),
      onFill: token("--on-fill"),
      monograms: MONOGRAM_FILLS.map(token),
      display: token("--display"),
      body: token("--body"),
    };
  }

  function avatarParts(playerIndex) {
    const { name } = participants.players[playerIndex];
    return { url: UI.avatarUrl(playerIndex), letter: UI.monogramLetter(name), tint: UI.monogramTint(name) };
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

  function dateInZone(timestamp) {
    const timeZone = (participants.timezone && participants.timezone.name) || DEFAULT_TIMEZONE;
    return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(timestamp));
  }

  // Everything the card shows, covering all modes and the whole archive like the racer page's summary strip.
  function cardModel(playerIndex) {
    const player = participants.players[playerIndex];
    const board = participants.leaderboards.find((item) => item.mode === ALL_MODES);
    const row = board.rows.find((item) => item.p === playerIndex);
    const record = recordsForMode(ALL_MODES, null)[playerIndex];
    const earnedAt = (definition) => player.badges[definition.id];
    const holders = (definition) => badgeHolderCounts.get(definition.id) ?? 0;
    // Rarest first; badges held by equally many racers keep the order they were won.
    const badges = participants.badges
      .filter((definition) => earnedAt(definition) !== undefined)
      .sort((a, b) => holders(a) - holders(b) || earnedAt(a) - earnedAt(b));
    const { nemesis } = RacerExtras.rivals(playerIndex);
    const firstRace = raceIndexesByPlayer[playerIndex][0];
    const rated = typeof player.rating === "number";
    return {
      name: player.name,
      link: `${location.origin}${location.pathname}${playerHash(player.id, ALL_MODES)}`,
      avatar: avatarParts(playerIndex),
      since: firstRace === undefined ? null : participants.races[firstRace].d,
      rank: ratingRank(playerIndex) ?? null,
      rankOf: participants.players.filter((other) => typeof other.rating === "number").length,
      rankDelta: ratingRankDeltas().get(playerIndex) ?? null,
      rating: rated ? player.rating : null,
      provisional: rated && player.finishes < participants.rating.provisional_finishes,
      ratingDays: RacerExtras.ratingDays(playerIndex),
      ratingBase: participants.rating.base,
      stats: [
        ["Races", integerFormatter.format(row ? row.races : 0)],
        ["Wins", integerFormatter.format(row ? row.wins : 0)],
        ["Podiums", integerFormatter.format(row ? row.podiums : 0)],
        ["H2H win", formatRate(winRate(record.wins, record.losses))],
      ],
      badges,
      badgeTotal: participants.badges.length,
      nextBadge: badges.length ? null : nextBadge(player),
      nemesis: nemesis
        ? { ...avatarParts(nemesis.opponent), name: participants.players[nemesis.opponent].name, record: `${nemesis.wins}–${nemesis.losses}` }
        : null,
      dataDate: dateInZone(participants.generated_at),
    };
  }

  // How many badge tiles fit in the row, keeping the last slot for a "+N" tile when they don't all fit.
  function badgeLayout(count) {
    const capacity = Math.floor((WIDTH - PAD - BADGE_LEFT + BADGE_GAP) / (BADGE_TILE + BADGE_GAP));
    const shown = count > capacity ? capacity - 1 : count;
    return { shown, extra: count - shown };
  }

  function loadImage(src) {
    return new Promise((resolve) => {
      const image = new Image();
      image.decoding = "async";
      image.addEventListener("load", () => resolve(image));
      image.addEventListener("error", () => resolve(null));
      image.src = src;
    });
  }

  function setText(ctx, font, color, { align = "left", spacing = "0px" } = {}) {
    ctx.font = font;
    ctx.fillStyle = color;
    ctx.textAlign = align;
    ctx.letterSpacing = spacing;
  }

  // Shortens text with an ellipsis until it fits; the font must already be set.
  function fitted(ctx, text, maxWidth) {
    if (ctx.measureText(text).width <= maxWidth) {
      return text;
    }
    const characters = Array.from(text);
    while (characters.length && ctx.measureText(`${characters.join("")}…`).width > maxWidth) {
      characters.pop();
    }
    return `${characters.join("")}…`;
  }

  function label(ctx, colors, text, x, y, color = colors.muted, align = "left") {
    setText(ctx, `700 10px ${colors.body}`, color, { align, spacing: "0.6px" });
    ctx.fillText(text.toUpperCase(), x, y);
    return ctx.measureText(text.toUpperCase()).width;
  }

  function drawAvatar(ctx, colors, image, parts, x, y, size) {
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x, y, size, size, 4);
    ctx.clip();
    if (image) {
      // Cover-crop to a centred square, like the page's object-fit: cover.
      const side = Math.min(image.naturalWidth, image.naturalHeight);
      ctx.drawImage(image, (image.naturalWidth - side) / 2, (image.naturalHeight - side) / 2, side, side, x, y, size, size);
    } else {
      ctx.fillStyle = colors.monograms[parts.tint - 1];
      ctx.fillRect(x, y, size, size);
      setText(ctx, `800 ${Math.round(size * 0.55)}px ${colors.display}`, colors.onFill, { align: "center" });
      ctx.textBaseline = "middle";
      ctx.fillText(parts.letter, x + size / 2, y + size / 2 + size * 0.04);
      ctx.textBaseline = "alphabetic";
    }
    ctx.restore();
    ctx.beginPath();
    ctx.roundRect(x + 0.5, y + 0.5, size - 1, size - 1, 4);
    ctx.strokeStyle = colors.line;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function drawFrame(ctx, colors) {
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(0, 0, WIDTH, HEIGHT, RADIUS);
    ctx.fillStyle = colors.panel;
    ctx.fill();
    ctx.clip();
    // The site's top rail: accent, gold and red bands.
    [[0, 0.46, colors.accent], [0.46, 0.72, colors.gold], [0.72, 1, colors.red]].forEach(([from, to, fill]) => {
      ctx.fillStyle = fill;
      ctx.fillRect(WIDTH * from, 0, WIDTH * (to - from), RAIL);
    });
    ctx.restore();
    ctx.beginPath();
    ctx.roundRect(1, 1, WIDTH - 2, HEIGHT - 2, RADIUS - 1);
    ctx.strokeStyle = colors.gold;
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  function rankDeltaText(delta) {
    if (delta === "new") {
      return "New this week";
    }
    return `${delta > 0 ? "▲" : "▼"}${Math.abs(delta)} in 7 days`;
  }

  // Returns the left edge of the block, so the name can stop short of it.
  function drawStanding(ctx, colors, model) {
    const right = WIDTH - PAD;
    const valueFont = `800 34px ${colors.display}`;
    if (model.rank === null) {
      setText(ctx, valueFont, colors.muted, { align: "right" });
      ctx.fillText("--", right, 56);
      const width = label(ctx, colors, "Unrated", right, 72, colors.muted, "right");
      return right - Math.max(width, ctx.measureText("--").width);
    }
    const ratingValue = integerFormatter.format(model.rating);
    const ratingLabel = model.provisional ? "Provisional" : "Rating";
    setText(ctx, valueFont, colors.ink, { align: "right" });
    ctx.fillText(ratingValue, right, 56);
    const ratingWidth = Math.max(ctx.measureText(ratingValue).width, label(ctx, colors, ratingLabel, right, 72, colors.muted, "right"));

    const rankRight = right - ratingWidth - 22;
    const rankValue = `#${integerFormatter.format(model.rank)}`;
    setText(ctx, valueFont, colors.accent, { align: "right" });
    ctx.fillText(rankValue, rankRight, 56);
    let rankWidth = Math.max(ctx.measureText(rankValue).width, label(ctx, colors, `of ${integerFormatter.format(model.rankOf)}`, rankRight, 72, colors.muted, "right"));
    if (model.rankDelta !== null) {
      const color = model.rankDelta === "new" ? colors.accent : model.rankDelta > 0 ? colors.positive : colors.negative;
      rankWidth = Math.max(rankWidth, label(ctx, colors, rankDeltaText(model.rankDelta), rankRight, 86, color, "right"));
    }
    return rankRight - rankWidth;
  }

  function drawIdentity(ctx, colors, model, images, standingLeft) {
    drawAvatar(ctx, colors, images.avatar, model.avatar, PAD, 22, AVATAR_SIZE);
    const left = PAD + AVATAR_SIZE + 14;
    const maxWidth = standingLeft - 16 - left;
    let size = 30;
    setText(ctx, `800 ${size}px ${colors.display}`, colors.ink);
    while (size > 20 && ctx.measureText(model.name).width > maxWidth) {
      size -= 1;
      ctx.font = `800 ${size}px ${colors.display}`;
    }
    ctx.fillText(fitted(ctx, model.name, maxWidth), left, 54);
    const since = model.since ? ` · since ${monthFormatter.format(new Date(`${model.since}T00:00:00Z`))}` : "";
    setText(ctx, `400 12px ${colors.body}`, colors.muted);
    ctx.fillText(fitted(ctx, `ALTTPR Step Ladder${since}`, maxWidth), left, 74);
  }

  function drawStats(ctx, colors, model, width) {
    model.stats.forEach(([name, value], index) => {
      const x = PAD + index * width;
      setText(ctx, `800 24px ${colors.display}`, colors.ink);
      ctx.fillText(value, x, 140);
      label(ctx, colors, name, x, 156);
    });
  }

  // A step line of the rating after each race day, like the racer page's rating chart.
  function drawSparkline(ctx, colors, model) {
    const days = model.ratingDays;
    const ratings = days.map((day) => day.rating);
    const low = Math.min(...ratings, model.ratingBase);
    const high = Math.max(...ratings, model.ratingBase);
    const padding = Math.max((high - low) * 0.1, 5);
    const halo = 6;
    const x = (day) =>
      SPARK.left + ((day - days[0].day) / Math.max(days.at(-1).day - days[0].day, 1)) * (SPARK.right - halo - SPARK.left);
    const y = (rating) => SPARK.bottom - ((rating - low + padding) / (high - low + 2 * padding)) * (SPARK.bottom - SPARK.top);

    label(ctx, colors, "Rating", SPARK.left, SPARK.top - 6);

    ctx.beginPath();
    ctx.moveTo(SPARK.left, y(model.ratingBase));
    ctx.lineTo(SPARK.right, y(model.ratingBase));
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = colors.seriesMuted;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.beginPath();
    days.forEach((day, index) => {
      if (index === 0) {
        ctx.moveTo(x(day.day), y(day.rating));
      } else {
        ctx.lineTo(x(day.day), y(days[index - 1].rating));
        ctx.lineTo(x(day.day), y(day.rating));
      }
    });
    ctx.strokeStyle = colors.accent;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.stroke();

    const peak = Math.max(...ratings);
    const current = days.at(-1);
    const endX = x(current.day);
    const endY = y(current.rating);
    if (peak > current.rating) {
      const peakDay = days[ratings.lastIndexOf(peak)];
      ctx.beginPath();
      ctx.arc(x(peakDay.day), y(peak), 3.5, 0, Math.PI * 2);
      ctx.fillStyle = colors.gold;
      ctx.fill();
      const text = `Peak ${integerFormatter.format(peak)}`;
      setText(ctx, `700 10px ${colors.body}`, colors.goldInk, { spacing: "0.6px" });
      const width = ctx.measureText(text.toUpperCase()).width;
      const labelX = Math.min(Math.max(x(peakDay.day) - width / 2, SPARK.left + 46), SPARK.right - width);
      label(ctx, colors, text, labelX, SPARK.top - 6, colors.goldInk);
    } else {
      label(ctx, colors, "All-time high", SPARK.right, SPARK.top - 6, colors.goldInk, "right");
    }
    ctx.beginPath();
    ctx.arc(endX, endY, halo, 0, Math.PI * 2);
    ctx.fillStyle = colors.panel;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = colors.accent;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(endX, endY, 3, 0, Math.PI * 2);
    ctx.fillStyle = colors.accent;
    ctx.fill();
  }

  function drawBadges(ctx, colors, model, images) {
    label(ctx, colors, "Badges", PAD, BADGE_TOP + 9);
    setText(ctx, `800 18px ${colors.display}`, colors.ink);
    ctx.fillText(`${model.badges.length} / ${model.badgeTotal}`, PAD, BADGE_TOP + 28);

    if (!model.badges.length) {
      const next = model.nextBadge;
      setText(ctx, `700 12px ${colors.body}`, next ? colors.ink : colors.muted);
      if (!next) {
        ctx.fillText("No badges yet", BADGE_LEFT, BADGE_TOP + 19);
        return;
      }
      const text = `Next badge: ${next.definition.label} · ${next.progress} / ${next.definition.target}`;
      ctx.fillText(fitted(ctx, text, WIDTH - PAD - BADGE_LEFT), BADGE_LEFT, BADGE_TOP + 12);
      const barWidth = 160;
      ctx.beginPath();
      ctx.roundRect(BADGE_LEFT, BADGE_TOP + 20, barWidth, 5, 2.5);
      ctx.fillStyle = colors.grid;
      ctx.fill();
      ctx.beginPath();
      ctx.roundRect(BADGE_LEFT, BADGE_TOP + 20, Math.max(5, barWidth * Math.min(next.share, 1)), 5, 2.5);
      ctx.fillStyle = colors.accent;
      ctx.fill();
      return;
    }

    const { shown, extra } = badgeLayout(model.badges.length);
    const inset = 3;
    for (let index = 0; index < shown + (extra ? 1 : 0); index += 1) {
      const x = BADGE_LEFT + index * (BADGE_TILE + BADGE_GAP);
      ctx.beginPath();
      ctx.roundRect(x + 0.75, BADGE_TOP + 0.75, BADGE_TILE - 1.5, BADGE_TILE - 1.5, 6);
      ctx.fillStyle = index < shown ? colors.panel : colors.grid;
      ctx.fill();
      if (index < shown) {
        ctx.strokeStyle = colors.gold;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        if (images.badges[index]) {
          ctx.drawImage(images.badges[index], x + inset, BADGE_TOP + inset, BADGE_TILE - 2 * inset, BADGE_TILE - 2 * inset);
        }
      } else {
        setText(ctx, `800 12px ${colors.body}`, colors.muted, { align: "center" });
        ctx.fillText(`+${extra}`, x + BADGE_TILE / 2, BADGE_TOP + 18);
      }
    }
  }

  // Draws right-aligned at the card's right padding and returns the width used.
  function drawNemesis(ctx, colors, nemesis, image, y) {
    setText(ctx, `800 12px ${colors.body}`, colors.ink);
    const recordWidth = ctx.measureText(nemesis.record).width;
    ctx.font = `700 12px ${colors.body}`;
    const name = fitted(ctx, nemesis.name, 150);
    const nameWidth = ctx.measureText(name).width;
    setText(ctx, `700 10px ${colors.body}`, colors.muted, { spacing: "0.6px" });
    const labelWidth = ctx.measureText("NEMESIS").width;
    const total = labelWidth + 8 + NEMESIS_AVATAR + 6 + nameWidth + 6 + recordWidth;
    let x = WIDTH - PAD - total;
    label(ctx, colors, "Nemesis", x, y);
    x += labelWidth + 8;
    drawAvatar(ctx, colors, image, nemesis, x, y - 13, NEMESIS_AVATAR);
    x += NEMESIS_AVATAR + 6;
    setText(ctx, `700 12px ${colors.body}`, colors.ink);
    ctx.fillText(name, x, y);
    x += nameWidth + 6;
    setText(ctx, `800 12px ${colors.body}`, colors.ink);
    ctx.fillText(nemesis.record, x, y);
    return total;
  }

  function drawHook(ctx, colors, model, images) {
    const y = 234;
    const nemesisWidth = model.nemesis ? drawNemesis(ctx, colors, model.nemesis, images.nemesis, y) + 20 : 0;
    const rarest = model.badges[0];
    if (!rarest) {
      return;
    }
    const maxRight = WIDTH - PAD - nemesisWidth;
    let x = PAD;
    setText(ctx, `800 12px ${colors.body}`, colors.gold);
    ctx.fillText("★", x, y);
    x += ctx.measureText("★").width + 5;
    x += label(ctx, colors, "Rarest", x, y, colors.goldInk) + 7;
    setText(ctx, `800 12px ${colors.body}`, colors.ink);
    const name = fitted(ctx, rarest.label, maxRight - x);
    ctx.fillText(name, x, y);
    x += ctx.measureText(name).width;
    const share = ` · ${UI.badgeShare(rarest.id)}`;
    setText(ctx, `400 12px ${colors.body}`, colors.muted);
    if (x + ctx.measureText(share).width <= maxRight) {
      ctx.fillText(share, x, y);
    }
  }

  function drawFooter(ctx, colors, model, images) {
    ctx.beginPath();
    ctx.moveTo(PAD, 254.5);
    ctx.lineTo(WIDTH - PAD, 254.5);
    ctx.strokeStyle = colors.grid;
    ctx.lineWidth = 1;
    ctx.stroke();
    const y = 278;
    let x = PAD;
    if (images.mark) {
      ctx.drawImage(images.mark, x, y - 14, 18, 18);
      x += 24;
    }
    setText(ctx, `800 15px ${colors.display}`, colors.accent, { spacing: "0.6px" });
    ctx.fillText(BRAND.toUpperCase(), x, y);
    x += ctx.measureText(BRAND.toUpperCase()).width + 8;
    const date = `Data as of ${formatRaceDate(model.dataDate)}`;
    setText(ctx, `400 11px ${colors.body}`, colors.muted, { align: "right" });
    ctx.fillText(date, WIDTH - PAD, y);
    const dateWidth = ctx.measureText(date).width;
    const site = `${location.host}${location.pathname}`.replace(/\/(index\.html)?$/, "");
    setText(ctx, `400 11px ${colors.body}`, colors.muted);
    ctx.fillText(fitted(ctx, site, WIDTH - PAD - dateWidth - 16 - x), x, y);
  }

  function drawCard(ctx, model, images) {
    const colors = palette();
    drawFrame(ctx, colors);
    const standingLeft = drawStanding(ctx, colors, model);
    drawIdentity(ctx, colors, model, images, standingLeft);
    const hasSparkline = model.ratingDays.length >= 2;
    drawStats(ctx, colors, model, hasSparkline ? STAT_WIDTH : (WIDTH - 2 * PAD) / model.stats.length);
    if (hasSparkline) {
      drawSparkline(ctx, colors, model);
    }
    drawBadges(ctx, colors, model, images);
    drawHook(ctx, colors, model, images);
    drawFooter(ctx, colors, model, images);
  }

  async function render(model) {
    const display = token("--display");
    const body = token("--body");
    // Without this the canvas silently draws in a fallback font.
    await Promise.all(
      [`700 12px ${display}`, `800 12px ${display}`, `400 12px ${body}`, `700 12px ${body}`, `800 12px ${body}`].map((font) => document.fonts.load(font)),
    );
    const { shown } = badgeLayout(model.badges.length);
    const [avatar, nemesis, mark, ...badges] = await Promise.all([
      model.avatar.url ? loadImage(model.avatar.url) : null,
      model.nemesis && model.nemesis.url ? loadImage(model.nemesis.url) : null,
      loadImage(CATEGORY_MARK),
      ...model.badges.slice(0, shown).map((definition) => loadImage(definition.still)),
    ]);
    const canvas = document.createElement("canvas");
    canvas.width = WIDTH * SCALE;
    canvas.height = HEIGHT * SCALE;
    const ctx = canvas.getContext("2d");
    ctx.scale(SCALE, SCALE);
    drawCard(ctx, model, { avatar, nemesis, mark, badges });
    return new Promise((resolve, reject) => {
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The canvas produced no image."))), "image/png");
    });
  }

  function summary(model) {
    const standing = model.rank === null ? "unrated" : `rank #${model.rank} of ${model.rankOf}, ${model.rating} rating`;
    return `${model.name}: ${standing}, ${model.badges.length} of ${model.badgeTotal} badges.`;
  }

  function fileName(model) {
    const slug = model.name
      .normalize("NFKD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
    return `${slug || "racer"}-step-ladder-${model.dataDate}.png`;
  }

  function canCopyImage() {
    return window.isSecureContext && typeof ClipboardItem === "function" && Boolean(navigator.clipboard && navigator.clipboard.write);
  }

  function action(text, primary, onClick) {
    const control = element("button", `share-card__action${primary ? " share-card__action--primary" : ""}`, text);
    control.type = "button";
    control.addEventListener("click", onClick);
    return control;
  }

  function actions(model, blob, objectUrl, say) {
    const file = new File([blob], fileName(model), { type: "image/png" });
    const failed = (message) => (error) => {
      console.error(message, error);
      say(message);
    };
    const controls = [];
    if (canCopyImage()) {
      controls.push(
        action("Copy image", true, () =>
          navigator.clipboard
            .write([new ClipboardItem({ "image/png": blob })])
            .then(() => say("Image copied. Paste it into Discord."), failed("Couldn't copy the image. Use Download instead.")),
        ),
      );
    }
    controls.push(
      action("Download", !canCopyImage(), () => {
        const link = element("a");
        link.href = objectUrl;
        link.download = file.name;
        link.click();
        say(`Saved as ${file.name}.`);
      }),
    );
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      controls.push(
        action("Share…", false, () =>
          navigator.share({ files: [file], title: `${model.name} on the Step Ladder` }).catch((error) => {
            if (error.name !== "AbortError") {
              failed("Couldn't open the share menu.")(error);
            }
          }),
        ),
      );
    }
    if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
      controls.push(
        action("Copy link", false, () =>
          navigator.clipboard.writeText(model.link).then(() => say("Link copied."), failed("Couldn't copy the link.")),
        ),
      );
    }
    return controls;
  }

  function open(playerIndex) {
    const model = cardModel(playerIndex);
    let closed = false;
    let objectUrl = null;
    const { panel } = UI.modalPanel({
      className: "share-card-panel",
      labelledBy: TITLE_ID,
      onClose: () => {
        closed = true;
        if (objectUrl) {
          URL.revokeObjectURL(objectUrl);
        }
      },
    });
    const title = element("h2", "ui-badge-panel__title", "Player card");
    title.id = TITLE_ID;
    const hint = element("p", "share-card__hint", `${model.name}'s ladder stats as a ${WIDTH} × ${HEIGHT} image, ready to paste into Discord.`);
    const frame = element("div", "share-card__frame share-card__frame--pending");
    const placeholder = element("p", "share-card__placeholder", "Drawing card…");
    frame.append(placeholder);
    const controls = element("div", "share-card__actions");
    const status = element("p", "share-card__status");
    status.setAttribute("aria-live", "polite");
    panel.append(title, hint, frame, controls, status);
    const say = (text) => {
      status.textContent = text;
    };

    render(model)
      .then((blob) => {
        if (closed) {
          return;
        }
        objectUrl = URL.createObjectURL(blob);
        const buttons = actions(model, blob, objectUrl, say);
        const preview = element("img", "share-card__preview");
        preview.src = objectUrl;
        preview.alt = summary(model);
        preview.width = WIDTH;
        preview.height = HEIGHT;
        frame.classList.remove("share-card__frame--pending");
        frame.replaceChildren(preview);
        controls.replaceChildren(...buttons);
      })
      .catch((error) => {
        console.error("Could not draw the share card.", error);
        placeholder.textContent = "Couldn't draw the card.";
        say("Couldn't draw the card.");
      });
  }

  function shareIcon() {
    const svg = svgElement("svg", { viewBox: "0 0 16 16", width: 13, height: 13, "aria-hidden": "true", focusable: "false" });
    svg.appendChild(
      svgElement("path", {
        d: "M8 10.5V1.75M4.75 5 8 1.75 11.25 5M3 8.5v5.75h10V8.5",
        fill: "none",
        stroke: "currentColor",
        "stroke-width": 1.75,
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      }),
    );
    return svg;
  }

  function button(playerIndex) {
    const control = element("button", "player-share");
    control.type = "button";
    control.setAttribute("aria-haspopup", "dialog");
    control.append(shareIcon(), element("span", null, "Create player card"));
    control.addEventListener("click", () => open(playerIndex));
    return control;
  }

  return { button, open };
})();
