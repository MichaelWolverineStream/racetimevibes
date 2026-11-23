(() => {
  const VERSION_ENDPOINT = "version.json";
  const VERSION_POLL_MS = 30 * 60 * 1000;
  const MIN_CHECK_GAP_MS = 5 * 60 * 1000;
  const RELOAD_FLAG = "racetimevibes:reloaded-for";

  const versionMeta = document.querySelector('meta[name="app-version"]');
  const loadedVersion = versionMeta ? versionMeta.content.trim() : "";
  let lastCheckAt = -Infinity;
  let pendingVersion = "";

  function alreadyReloadedFor(version) {
    try {
      return window.sessionStorage.getItem(RELOAD_FLAG) === version;
    } catch (error) {
      return false;
    }
  }

  function rememberReload(version) {
    try {
      window.sessionStorage.setItem(RELOAD_FLAG, version);
    } catch (error) {
      console.warn("Version reload guard unavailable.", error);
    }
  }

  function reloadFor(version) {
    rememberReload(version);
    window.location.reload();
  }

  async function publishedVersion() {
    const response = await fetch(VERSION_ENDPOINT, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Version request failed with HTTP ${response.status}.`);
    }
    const payload = await response.json();
    return typeof payload.version === "string" ? payload.version.trim() : "";
  }

  // No check runs within MIN_CHECK_GAP_MS of the previous one, so switching tabs back
  // and forth costs at most one request per gap. performance.now() never jumps with
  // the system clock. A poll that finds a new version while the page is on screen
  // keeps it in pendingVersion and reloads when the tab is hidden, without checking
  // again, so the page never reloads under someone reading it.
  async function checkForUpdate({ deferWhileVisible = false } = {}) {
    if (!loadedVersion) {
      return;
    }
    const now = performance.now();
    if (now - lastCheckAt < MIN_CHECK_GAP_MS) {
      return;
    }
    lastCheckAt = now;
    try {
      const latest = await publishedVersion();
      if (!latest || latest === loadedVersion) {
        return;
      }
      // Without this guard a cached index.html keeps reporting the old version and reloads forever.
      if (alreadyReloadedFor(latest)) {
        return;
      }
      if (deferWhileVisible && !document.hidden) {
        pendingVersion = latest;
        return;
      }
      reloadFor(latest);
    } catch (error) {
      console.warn(error);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (pendingVersion && document.hidden) {
      reloadFor(pendingVersion);
      return;
    }
    if (!document.hidden) {
      checkForUpdate();
    }
  });
  window.setInterval(() => checkForUpdate({ deferWhileVisible: true }), VERSION_POLL_MS);
  checkForUpdate();
})();
