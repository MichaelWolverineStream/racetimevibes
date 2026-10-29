const EasterEggs = (() => {
  const KONAMI = ["arrowup", "arrowup", "arrowdown", "arrowdown", "arrowleft", "arrowright", "arrowleft", "arrowright", "b", "a"];
  const MODIFIER_KEYS = new Set(["shift", "control", "alt", "meta", "capslock"]);
  const PIECE_IDS = ["konami", "pedestal", "credit", "palindrome"];
  const PIECES_KEY = "pieces";
  const RAIL_TINT_MS = 1200;
  // Heart outline on a 10x8 pixel grid; quarters split at x = 5 and y = 4.
  const HEART_POINTS = "1,0 4,0 4,1 6,1 6,0 9,0 9,1 10,1 10,4 9,4 9,5 8,5 8,6 7,6 7,7 6,7 6,8 4,8 4,7 3,7 3,6 2,6 2,5 1,5 1,4 0,4 0,1 1,1";
  const HEART_QUARTERS = [
    { x: 0, y: 0 },
    { x: 5, y: 0 },
    { x: 0, y: 4 },
    { x: 5, y: 4 },
  ];

  let started = false;
  let konamiStep = 0;
  let overlayOpen = false;
  let railTimer = null;

  function guarded(handler) {
    return (...args) => {
      try {
        handler(...args);
      } catch (error) {
        console.warn("Easter egg failed.", error);
      }
    };
  }

  function foundPieces() {
    return Store.get(PIECES_KEY, []).filter((id) => PIECE_IDS.includes(id));
  }

  function toast(text, onClose) {
    if (typeof UI !== "undefined") {
      UI.dialogBox(text, { onClose });
    }
  }

  // Opening a dialog closes the current one, so a follow-up toast waits and yields to anything newer.
  function toastWhenFree(text) {
    setTimeout(guarded(() => {
      if (!document.querySelector(".ui-dialog")) {
        toast(text);
      }
    }), 0);
  }

  function pieceText(count) {
    return count >= PIECE_IDS.length ? "You got a Heart Container!" : `You found a Piece of Heart! (${count}/${PIECE_IDS.length})`;
  }

  function heartLabel(count) {
    return count >= PIECE_IDS.length ? "Heart Container" : `Pieces of Heart: ${count} of ${PIECE_IDS.length}`;
  }

  function renderHud() {
    const hud = document.getElementById("heart-hud");
    const found = foundPieces();
    if (!hud || !found.length) {
      return;
    }
    const complete = found.length >= PIECE_IDS.length;
    const svg = svgElement("svg", { class: "ee-heart", viewBox: "0 0 10 8", width: 22, height: 18, "aria-hidden": "true" });
    const clip = svgElement("clipPath", { id: "ee-heart-clip" });
    clip.appendChild(svgElement("polygon", { points: HEART_POINTS }));
    svg.appendChild(svgElement("defs", {})).appendChild(clip);
    const quarters = svgElement("g", { "clip-path": "url(#ee-heart-clip)" });
    HEART_QUARTERS.forEach((quarter, index) => {
      const filled = complete || found.includes(PIECE_IDS[index]);
      quarters.appendChild(svgElement("rect", {
        class: filled ? "ee-heart__quarter ee-heart__quarter--found" : "ee-heart__quarter",
        x: quarter.x,
        y: quarter.y,
        width: 5,
        height: 4,
      }));
    });
    svg.appendChild(quarters);
    svg.appendChild(svgElement("polygon", { class: "ee-heart__outline", points: HEART_POINTS }));

    const label = heartLabel(found.length);
    hud.classList.toggle("heart-hud--complete", complete);
    hud.setAttribute("role", "img");
    hud.setAttribute("aria-label", label);
    hud.title = label;
    hud.replaceChildren(svg);
    hud.hidden = false;
  }

  // Returns true when the piece is new.
  function award(id) {
    const found = foundPieces();
    if (found.includes(id)) {
      return false;
    }
    Store.set(PIECES_KEY, [...found, id]);
    renderHud();
    return true;
  }

  function awardWithToast(id) {
    if (award(id)) {
      toast(pieceText(foundPieces().length));
    }
  }

  function triforce() {
    const svg = svgElement("svg", { class: "ee-triforce__svg", viewBox: "0 0 120 104", "aria-hidden": "true" });
    [
      ["top", "60,2 90,52 30,52"],
      ["left", "30,52 60,102 0,102"],
      ["right", "90,52 120,102 60,102"],
    ].forEach(([name, points]) => {
      svg.appendChild(svgElement("polygon", { class: `ee-triforce__piece ee-triforce__piece--${name}`, points }));
    });
    const wrap = document.createElement("div");
    wrap.className = "ee-triforce";
    wrap.appendChild(svg);
    return wrap;
  }

  // The modal dialog owns focus, Esc and click-to-close; the Triforce just follows its lifetime.
  function openOverlay(onClose) {
    if (typeof UI === "undefined" || overlayOpen) {
      return;
    }
    const wrap = triforce();
    overlayOpen = true;
    UI.dialogBox("It's dangerous to go alone! Take this.", {
      modal: true,
      onClose: () => {
        overlayOpen = false;
        wrap.remove();
        if (onClose) {
          onClose();
        }
      },
    });
    document.body.appendChild(wrap);
  }

  function onKonami() {
    const isNew = award("konami");
    openOverlay(() => {
      if (isNew) {
        toastWhenFree(pieceText(foundPieces().length));
      }
    });
  }

  function isTypingTarget(target) {
    return target instanceof Element && (target.isContentEditable || Boolean(target.closest("input, textarea, select, [contenteditable]")));
  }

  function onKeydown(event) {
    const key = String(event.key).toLowerCase();
    if (event.repeat || event.ctrlKey || event.altKey || event.metaKey || MODIFIER_KEYS.has(key) || isTypingTarget(event.target) || overlayOpen) {
      return;
    }
    if (key === KONAMI[konamiStep]) {
      konamiStep += 1;
    } else {
      konamiStep = key === KONAMI[0] ? 1 : 0;
    }
    if (konamiStep === KONAMI.length) {
      konamiStep = 0;
      onKonami();
    }
  }

  function tintRail() {
    const rail = document.querySelector(".top-rail");
    if (!rail) {
      return;
    }
    clearTimeout(railTimer);
    rail.classList.remove("top-rail--ganon");
    void rail.offsetWidth;
    rail.classList.add("top-rail--ganon");
    railTimer = setTimeout(() => rail.classList.remove("top-rail--ganon"), RAIL_TINT_MS);
  }

  const SEARCH_EGGS = {
    ganon() {
      tintRail();
      toast("Dun dun dun…");
    },
    triforce() {
      openOverlay();
    },
    pedestal() {
      const isNew = award("pedestal");
      toast("You drew the Master Sword!", isNew ? () => toastWhenFree(pieceText(foundPieces().length)) : undefined);
    },
  };

  function watchSearch(id) {
    const input = document.getElementById(id);
    if (!input) {
      return;
    }
    let previous = "";
    input.addEventListener("input", guarded(() => {
      const value = input.value.trim().toLowerCase();
      if (value === previous) {
        return;
      }
      previous = value;
      if (Object.hasOwn(SEARCH_EGGS, value)) {
        SEARCH_EGGS[value]();
      }
    }));
  }

  function init() {
    if (started) {
      return;
    }
    started = true;
    guarded(() => {
      renderHud();
      document.addEventListener("keydown", guarded(onKeydown));
      document.addEventListener("click", guarded((event) => {
        if (event.detail === 3 && event.target instanceof Element && event.target.closest(".footer-credit")) {
          awardWithToast("credit");
        }
      }));
      document.addEventListener("racetimevibes:palindrome", guarded(() => awardWithToast("palindrome")));
      watchSearch("leaderboard-search");
      watchSearch("player-search");
    })();
  }

  return { init };
})();
