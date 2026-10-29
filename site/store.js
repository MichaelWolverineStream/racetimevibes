const STORAGE_PREFIX = "racetimevibes:";
const FOLLOWING_KEY = "following";
const FOLLOWING_LIMIT = 50;
const FOLLOWING_EVENT = "racetimevibes:following";

function sameShape(value, fallback) {
  if (fallback === null || fallback === undefined) {
    return true;
  }
  if (value === null || Array.isArray(value) !== Array.isArray(fallback)) {
    return false;
  }
  return typeof value === typeof fallback;
}

// Storage can be blocked or full, so every backend falls back to a per-page Map.
function createStore(backendName) {
  const memory = new Map();

  function backend() {
    try {
      return window[backendName];
    } catch (error) {
      return null;
    }
  }

  // The memory copy stands in only when storage itself fails, so a key another tab
  // removed reads as missing here too.
  function readRaw(key) {
    try {
      return backend().getItem(STORAGE_PREFIX + key);
    } catch (error) {
      console.warn(`${backendName} unavailable, using memory.`, error);
      return memory.has(key) ? memory.get(key) : null;
    }
  }

  function get(key, fallback = null) {
    const raw = readRaw(key);
    if (raw === null) {
      return fallback;
    }
    try {
      const value = JSON.parse(raw);
      return sameShape(value, fallback) ? value : fallback;
    } catch (error) {
      console.warn(`Ignoring corrupt value for ${key}.`, error);
      return fallback;
    }
  }

  function set(key, value) {
    const raw = JSON.stringify(value);
    memory.set(key, raw);
    try {
      backend().setItem(STORAGE_PREFIX + key, raw);
    } catch (error) {
      console.warn(`${backendName} unavailable, kept in memory.`, error);
    }
  }

  function remove(key) {
    memory.delete(key);
    try {
      backend().removeItem(STORAGE_PREFIX + key);
    } catch (error) {
      console.warn(`${backendName} unavailable.`, error);
    }
  }

  return { get, set, remove };
}

const Store = (() => {
  const local = createStore("localStorage");
  return { ...local, session: createStore("sessionStorage") };
})();

const Following = (() => {
  // Parsed once and kept until this tab or another one changes the list; every star asks.
  let cached = null;

  function list() {
    if (cached === null) {
      const stored = Store.get(FOLLOWING_KEY, []);
      cached = [...new Set(stored.filter((id) => typeof id === "string"))].slice(0, FOLLOWING_LIMIT);
    }
    return cached;
  }

  function ids() {
    return [...list()];
  }

  function has(userId) {
    return list().includes(userId);
  }

  function announce(userId) {
    document.dispatchEvent(new CustomEvent(FOLLOWING_EVENT, { detail: { userId, following: userId === null ? null : has(userId) } }));
  }

  // Returns the new following state; following past the cap is refused and returns false.
  function toggle(userId) {
    const current = list();
    if (current.includes(userId)) {
      cached = current.filter((id) => id !== userId);
    } else if (current.length < FOLLOWING_LIMIT) {
      cached = [...current, userId];
    }
    Store.set(FOLLOWING_KEY, cached);
    announce(userId);
    return has(userId);
  }

  // Another tab changed the list.
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_PREFIX + FOLLOWING_KEY || event.key === null) {
      cached = null;
      announce(null);
    }
  });

  return { ids, has, toggle };
})();
