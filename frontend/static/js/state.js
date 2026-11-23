/**
 * Reactive State Management.
 * Lightweight pub/sub store for search, filters, drawer, and user preferences.
 * Persists recent queries in sessionStorage (no PHI beyond query text).
 */

const RECENT_QUERIES_KEY = "ehr_recent_queries_v1";
const DARK_MODE_KEY = "ehr_theme_mode_v1";

class StateStore {
  constructor() {
    this._listeners = new Set();

    // Load recent queries from sessionStorage
    let recent = [];
    try {
      const stored = sessionStorage.getItem(RECENT_QUERIES_KEY);
      if (stored) {
        recent = JSON.parse(stored);
      }
    } catch {
      recent = [];
    }

    // Load dark mode
    let dark = false;
    try {
      const storedTheme = localStorage.getItem(DARK_MODE_KEY);
      if (storedTheme !== null) {
        dark = storedTheme === "dark";
      } else {
        dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      }
    } catch {
      dark = false;
    }

    this._state = {
      query: "",
      resourceType: "All",
      startDate: null,
      endDate: null,
      sortBy: "score", // "score" | "date"
      isLoading: false,
      error: null,
      results: [],
      totalPatients: 0,
      selectedPatientId: null,
      selectedPatientDetail: null,
      isDrawerOpen: false,
      isDrawerLoading: false,
      drawerError: null,
      recentQueries: recent,
      darkMode: dark,
    };
  }

  getState() {
    return this._state;
  }

  setState(partialState) {
    const prevState = { ...this._state };
    this._state = { ...this._state, ...partialState };

    // Persist recent queries if updated
    if (partialState.recentQueries) {
      try {
        sessionStorage.setItem(
          RECENT_QUERIES_KEY,
          JSON.stringify(this._state.recentQueries.slice(0, 5))
        );
      } catch {
        // quota exceeded or private mode
      }
    }

    // Persist dark mode if updated
    if (partialState.darkMode !== undefined) {
      try {
        localStorage.setItem(DARK_MODE_KEY, this._state.darkMode ? "dark" : "light");
        if (this._state.darkMode) {
          document.documentElement.classList.add("dark");
        } else {
          document.documentElement.classList.remove("dark");
        }
      } catch {
        // storage disabled
      }
    }

    this._notify(this._state, prevState);
  }

  addRecentQuery(query) {
    const q = query.trim();
    if (!q || q.length < 2) return;

    const current = this._state.recentQueries.filter(item => item.toLowerCase() !== q.toLowerCase());
    const updated = [q, ...current].slice(0, 5);
    this.setState({ recentQueries: updated });
  }

  clearRecentQueries() {
    this.setState({ recentQueries: [] });
  }

  subscribe(listener) {
    this._listeners.add(listener);
    return () => this._listeners.delete(listener);
  }

  _notify(state, prevState) {
    for (const listener of this._listeners) {
      try {
        listener(state, prevState);
      } catch (err) {
        // ignore listener errors
      }
    }
  }
}

export const store = new StateStore();
