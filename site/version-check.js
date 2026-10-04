const VERSION_ENDPOINT = "version.json";
const VERSION_POLL_MS = 5 * 60 * 1000;
const RELOAD_FLAG = "racetimevibes:reloaded-for";

const versionMeta = document.querySelector('meta[name="app-version"]');
const loadedVersion = versionMeta ? versionMeta.content.trim() : "";

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

async function checkForUpdate() {
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
    rememberReload(latest);
    window.location.reload();
  } catch (error) {
    console.warn(error);
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    checkForUpdate();
  }
});
window.setInterval(checkForUpdate, VERSION_POLL_MS);
checkForUpdate();
