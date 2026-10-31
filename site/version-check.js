(() => {
  const VERSION_ENDPOINT = "version.json";
  const VERSION_POLL_MS = 5 * 60 * 1000;
  const RELOAD_FLAG = "racetimevibes:reloaded-for";

  const versionMeta = document.querySelector('meta[name="app-version"]');
  const loadedVersion = versionMeta ? versionMeta.content.trim() : "";
  let reloadPending = false;

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

  async function publishedVersion() {
    const response = await fetch(VERSION_ENDPOINT, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Version request failed with HTTP ${response.status}.`);
    }
    const payload = await response.json();
    return typeof payload.version === "string" ? payload.version.trim() : "";
  }

  // A poll that finds a new version while the page is on screen waits for the next
  // visibility change, so the page never reloads under someone reading it.
  async function checkForUpdate({ deferWhileVisible = false } = {}) {
    if (!loadedVersion) {
      return;
    }
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
        reloadPending = true;
        return;
      }
      rememberReload(latest);
      window.location.reload();
    } catch (error) {
      console.warn(error);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (reloadPending || !document.hidden) {
      checkForUpdate();
    }
  });
  window.setInterval(() => checkForUpdate({ deferWhileVisible: true }), VERSION_POLL_MS);
  checkForUpdate();
})();
