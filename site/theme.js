// Loaded blocking in <head>, after the theme-color meta and before the stylesheets, so the theme is set
// before the first paint.
// store.js is deferred and loads later, so this reads and writes storage itself, in Store's format:
// a JSON value under STORAGE_PREFIX + "theme". The prefix must match STORAGE_PREFIX in store.js.
(() => {
  const STORAGE_KEY = "racetimevibes:theme";
  const THEMES = ["light", "dark"];
  const root = document.documentElement;
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function saved() {
    let raw;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      console.warn("localStorage unavailable; the theme follows the system.", error);
      return null;
    }
    if (raw === null) {
      return null;
    }
    try {
      const value = JSON.parse(raw);
      return THEMES.includes(value) ? value : null;
    } catch (error) {
      console.warn("Ignoring corrupt value for theme.", error);
      return null;
    }
  }

  function save(theme) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(theme));
    } catch (error) {
      console.warn("localStorage unavailable; the theme lasts for this visit only.", error);
    }
  }

  // A choice made on this page, or saved by an earlier visit or another tab; null follows the system.
  let choice = saved();

  function current() {
    return choice ?? (systemDark.matches ? "dark" : "light");
  }

  // The browser toolbar takes the nav band's colour from the first run, before the stylesheets load, so
  // it can't be read from them. Must match --brand-fill in each theme (styles.css); tests/test_site_theme.py checks.
  const BROWSER_CHROME = { light: "#12634a", dark: "#1c533e" };
  const browserChrome = document.querySelector('meta[name="theme-color"]');

  function apply(theme) {
    root.dataset.theme = theme;
    browserChrome.content = BROWSER_CHROME[theme];
    const button = document.getElementById("theme-toggle");
    if (button) {
      button.setAttribute("aria-pressed", String(theme === "dark"));
    }
  }

  // The page cross-fades into the other theme. The reduced-motion rule in styles.css can't reach the
  // ::view-transition pseudo-elements, so reduced motion is checked here and switches instantly.
  function toggle() {
    const next = current() === "dark" ? "light" : "dark";
    choice = next;
    save(next);
    if (typeof document.startViewTransition !== "function" || reducedMotion.matches) {
      apply(next);
      return;
    }
    document.startViewTransition(() => apply(next));
  }

  apply(current());

  systemDark.addEventListener("change", () => {
    if (choice === null) {
      apply(current());
    }
  });

  // Another tab chose a theme, or cleared storage.
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY || event.key === null) {
      choice = saved();
      apply(current());
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("theme-toggle").addEventListener("click", toggle);
    apply(current());
  });
})();
