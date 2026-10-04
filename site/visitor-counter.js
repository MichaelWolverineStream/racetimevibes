const VISIT_KEY = "racetimevibes:visits";
const COUNTER_EPOCH_MS = Date.UTC(2026, 8, 11);
const TEN_MINUTES_MS = 10 * 60 * 1000;
const DIGIT_PADDING = 7;

// Each digit is a 5x7 LED grid, one string per row, "1" meaning a lit dot.
const DIGIT_GLYPHS = {
  0: ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
  1: ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
  2: ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
  3: ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
  4: ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
  5: ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
  6: ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
  7: ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
  8: ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
  9: ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
};

function tenMinuteTicksSinceEpoch() {
  const elapsed = Date.now() - COUNTER_EPOCH_MS;
  return elapsed > 0 ? Math.floor(elapsed / TEN_MINUTES_MS) : 0;
}

function bumpVisitCount() {
  try {
    const stored = Number.parseInt(window.localStorage.getItem(VISIT_KEY) ?? "", 10);
    const visits = Number.isInteger(stored) && stored > 0 ? stored + 1 : 1;
    window.localStorage.setItem(VISIT_KEY, String(visits));
    return visits;
  } catch (error) {
    console.warn("Visit counter storage unavailable.", error);
    return 1;
  }
}

function digitCell(digit) {
  const cell = document.createElement("span");
  cell.className = "hit-counter__digit";
  for (const row of DIGIT_GLYPHS[digit]) {
    for (const pixel of row) {
      const dot = document.createElement("i");
      dot.className =
        pixel === "1" ? "hit-counter__dot hit-counter__dot--on" : "hit-counter__dot";
      cell.append(dot);
    }
  }
  return cell;
}

function renderCounter() {
  const container = document.querySelector("#hit-counter-digits");
  if (!container) {
    return;
  }
  const total = tenMinuteTicksSinceEpoch() + bumpVisitCount();
  const digits = String(total).padStart(DIGIT_PADDING, "0");
  container.replaceChildren(...Array.from(digits, digitCell));
  container.setAttribute("aria-label", `${total.toLocaleString()} visitors`);
}

renderCounter();
