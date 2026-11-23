/**
 * Main Application Orchestrator for EHR Media Intelligence Interface.
 * Coordinates reactive state, debounced API calls, AbortController cancellation,
 * DOM updates, drawer interactions, and keyboard shortcuts.
 */

import { store } from "./state.js";
import { searchRecords, getPatientDetail } from "./api.js";
import { el, clearChildren } from "./utils/dom.js";
import { setTriggerElement, announceLive, setupGlobalKeybindings } from "./utils/a11y.js";
import { createCardSkeleton, createInitialEmptyState, createNoResultsState } from "./components/skeletons.js";
import { createPatientCard } from "./components/patient_card.js";
import { initPatientDrawer, openPatientDrawer, showToast } from "./components/patient_drawer.js";

let activeSearchAbort = null;
let debounceTimer = null;

/**
 * Executes patient search with current query and filters.
 * Cancels any active in-flight search request via AbortController.
 */
async function triggerSearch(immediate = false) {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }

  const run = async () => {
    const state = store.getState();
    const query = state.query.trim();

    if (!query) {
      store.setState({
        isLoading: false,
        results: [],
        totalPatients: 0,
        error: null,
      });
      return;
    }

    // Cancel prior in-flight search
    if (activeSearchAbort) {
      activeSearchAbort.abort();
    }
    activeSearchAbort = new AbortController();

    store.setState({ isLoading: true, error: null });

    try {
      const response = await searchRecords(
        {
          query,
          resourceType: state.resourceType,
          startDate: state.startDate,
          endDate: state.endDate,
          limit: 5,
        },
        activeSearchAbort.signal
      );

      // Record in recent searches if successful
      store.addRecentQuery(query);

      store.setState({
        isLoading: false,
        results: response.results || [],
        totalPatients: response.total_patients || 0,
        error: null,
      });

      announceLive(
        `Found ${response.total_patients} patient results for clinical query ${query}.`
      );
    } catch (err) {
      if (err.name === "AbortedError") {
        // Request aborted because new search started; leave loading state to newer call
        return;
      }
      store.setState({
        isLoading: false,
        error: err.detail || err.message || "Failed to search clinical records.",
        results: [],
        totalPatients: 0,
      });
      announceLive(`Clinical search error: ${err.message}`, "assertive");
    }
  };

  if (immediate) {
    await run();
  } else {
    debounceTimer = setTimeout(run, 300);
  }
}

/**
 * Handles patient selection and drawer opening.
 */
async function handleOpenPatient(patientId, triggerEl = null) {
  if (triggerEl) {
    setTriggerElement(triggerEl);
  } else if (document.activeElement) {
    setTriggerElement(document.activeElement);
  }

  store.setState({ isDrawerLoading: true });
  showToast("Loading clinical profile...", "info");

  try {
    const detail = await getPatientDetail(patientId);
    store.setState({
      selectedPatientId: patientId,
      selectedPatientDetail: detail,
      isDrawerOpen: true,
      isDrawerLoading: false,
    });
    openPatientDrawer(detail, triggerEl);
  } catch (err) {
    store.setState({ isDrawerLoading: false });
    showToast(`Failed to open patient drawer: ${err.message}`, "error");
  }
}

/**
 * Renders the results list based on current state.
 */
function renderResults(state) {
  const container = document.getElementById("results-cards-container");
  const heading = document.getElementById("results-heading");
  const badge = document.getElementById("results-count-badge");
  const clearFiltersBtn = document.getElementById("clear-filters-btn");
  const searchSpinner = document.getElementById("search-spinner");
  const searchClearBtn = document.getElementById("search-clear-btn");
  const searchInput = document.getElementById("clinical-search-input");

  // Sync search input value if changed programmatically
  if (searchInput.value !== state.query) {
    searchInput.value = state.query;
  }

  // Toggle search clear button
  if (state.query) {
    searchClearBtn.classList.remove("hidden");
  } else {
    searchClearBtn.classList.add("hidden");
  }

  // Toggle search spinner
  if (state.isLoading) {
    searchSpinner.classList.remove("hidden");
  } else {
    searchSpinner.classList.add("hidden");
  }

  // Toggle clear filters button visibility
  const hasActiveFilters =
    state.resourceType !== "All" || Boolean(state.startDate) || Boolean(state.endDate);
  if (hasActiveFilters) {
    clearFiltersBtn.classList.remove("hidden");
  } else {
    clearFiltersBtn.classList.add("hidden");
  }

  clearChildren(container);

  // 1. Loading State
  if (state.isLoading) {
    heading.textContent = `Searching clinical cohort for "${state.query}"...`;
    badge.textContent = "Loading...";
    for (let i = 0; i < 3; i++) {
      container.appendChild(createCardSkeleton());
    }
    return;
  }

  // 2. Initial Empty State
  if (!state.query.trim()) {
    heading.textContent = "Clinical Cohort Exploration";
    badge.textContent = "Cohort Ready";
    container.appendChild(
      createInitialEmptyState((suggestedQuery) => {
        store.setState({ query: suggestedQuery });
        triggerSearch(true);
      })
    );
    return;
  }

  // 3. No Results State
  if (state.results.length === 0) {
    heading.textContent = `Results for "${state.query}"`;
    badge.textContent = "0 Patients";
    container.appendChild(
      createNoResultsState(state.query, hasActiveFilters ? resetFilters : null)
    );
    return;
  }

  // 4. Populated Results with Sorting
  let sorted = [...state.results];
  if (state.sortBy === "date") {
    sorted.sort((a, b) => {
      // Undated records placed strictly last
      if (!a.record_date && !b.record_date) return 0;
      if (!a.record_date) return 1;
      if (!b.record_date) return -1;
      return new Date(b.record_date) - new Date(a.record_date);
    });
  } else {
    // Default score descending
    sorted.sort((a, b) => Number(b.score) - Number(a.score));
  }

  heading.textContent = `Cohort Results for "${state.query}"`;
  badge.textContent = `${sorted.length} Patient${sorted.length === 1 ? "" : "s"}`;

  for (const item of sorted) {
    container.appendChild(createPatientCard(item, state.query, handleOpenPatient));
  }
}

/**
 * Resets all active filters.
 */
function resetFilters() {
  document.getElementById("resource-filter-select").value = "All";
  const startIn = document.getElementById("date-start-input");
  const endIn = document.getElementById("date-end-input");
  startIn.value = "";
  endIn.value = "";
  document.getElementById("clear-start-date-btn")?.classList.add("hidden");
  document.getElementById("clear-end-date-btn")?.classList.add("hidden");
  store.setState({
    resourceType: "All",
    startDate: null,
    endDate: null,
  });
  triggerSearch(true);
}

/**
 * Initializes DOM event listeners and binds reactive store.
 */
function init() {
  const searchInput = document.getElementById("clinical-search-input");
  const searchClearBtn = document.getElementById("search-clear-btn");
  const resourceSelect = document.getElementById("resource-filter-select");
  const startDateInput = document.getElementById("date-start-input");
  const endDateInput = document.getElementById("date-end-input");
  const clearFiltersBtn = document.getElementById("clear-filters-btn");
  const sortSelect = document.getElementById("sort-select");
  const themeToggleBtn = document.getElementById("theme-toggle-btn");
  const errorContainer = document.getElementById("error-container");
  const errorMessage = document.getElementById("error-message");
  const errorDismissBtn = document.getElementById("error-dismiss-btn");

  // Apply dark mode on initial boot
  const initialDark = store.getState().darkMode;
  if (initialDark) {
    document.documentElement.classList.add("dark");
    document.getElementById("theme-icon-sun").classList.remove("hidden");
    document.getElementById("theme-icon-moon").classList.add("hidden");
  }

  // Search input typing (debounced)
  searchInput.addEventListener("input", (e) => {
    store.setState({ query: e.target.value });
    triggerSearch(false);
  });

  // Enter key searches immediately
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      store.setState({ query: searchInput.value });
      triggerSearch(true);
    }
  });

  // Clear button
  searchClearBtn.addEventListener("click", () => {
    searchInput.value = "";
    store.setState({ query: "" });
    triggerSearch(true);
    searchInput.focus();
  });

  // Resource filter change
  resourceSelect.addEventListener("change", (e) => {
    store.setState({ resourceType: e.target.value });
    triggerSearch(true);
  });

  // Date range inputs with clear controls
  const clearStartDateBtn = document.getElementById("clear-start-date-btn");
  const clearEndDateBtn = document.getElementById("clear-end-date-btn");

  const syncDateClearButtons = () => {
    if (startDateInput.value) {
      clearStartDateBtn?.classList.remove("hidden");
    } else {
      clearStartDateBtn?.classList.add("hidden");
    }
    if (endDateInput.value) {
      clearEndDateBtn?.classList.remove("hidden");
    } else {
      clearEndDateBtn?.classList.add("hidden");
    }
  };

  startDateInput.addEventListener("change", (e) => {
    syncDateClearButtons();
    store.setState({ startDate: e.target.value || null });
    triggerSearch(true);
  });
  endDateInput.addEventListener("change", (e) => {
    syncDateClearButtons();
    store.setState({ endDate: e.target.value || null });
    triggerSearch(true);
  });

  if (clearStartDateBtn) {
    clearStartDateBtn.addEventListener("click", () => {
      startDateInput.value = "";
      syncDateClearButtons();
      store.setState({ startDate: null });
      triggerSearch(true);
    });
  }

  if (clearEndDateBtn) {
    clearEndDateBtn.addEventListener("click", () => {
      endDateInput.value = "";
      syncDateClearButtons();
      store.setState({ endDate: null });
      triggerSearch(true);
    });
  }

  // Reset filters
  clearFiltersBtn.addEventListener("click", resetFilters);

  // Sorting
  sortSelect.addEventListener("change", (e) => {
    store.setState({ sortBy: e.target.value });
  });

  // Suggestion pills
  document.querySelectorAll(".query-pill-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const q = btn.dataset.query;
      if (q) {
        store.setState({ query: q });
        triggerSearch(true);
      }
    });
  });

  // Dark mode toggle
  themeToggleBtn.addEventListener("click", () => {
    const current = store.getState().darkMode;
    const next = !current;
    store.setState({ darkMode: next });
    if (next) {
      document.getElementById("theme-icon-sun").classList.remove("hidden");
      document.getElementById("theme-icon-moon").classList.add("hidden");
    } else {
      document.getElementById("theme-icon-sun").classList.add("hidden");
      document.getElementById("theme-icon-moon").classList.remove("hidden");
    }
  });

  // Error dismiss
  errorDismissBtn.addEventListener("click", () => {
    store.setState({ error: null });
  });

  // Drawer modal setup
  const drawer = initPatientDrawer({
    onClose: () => {
      store.setState({ isDrawerOpen: false, selectedPatientId: null });
    },
  });

  // Global keyboard shortcuts: '/' to focus search, 'Escape' to close drawer
  setupGlobalKeybindings({
    onSlash: () => {
      searchInput.focus();
      searchInput.select();
    },
    onEscape: () => {
      if (store.getState().isDrawerOpen) {
        drawer.close();
      }
    },
  });

  // Subscribe to state changes
  store.subscribe((state, prevState) => {
    // Render error if state.error changed
    if (state.error) {
      errorMessage.textContent = state.error;
      errorContainer.classList.remove("hidden");
    } else {
      errorContainer.classList.add("hidden");
    }

    // Only re-render results list when search results, loading, query, or sort changed
    const resultsChanged =
      !prevState ||
      state.results !== prevState.results ||
      state.isLoading !== prevState.isLoading ||
      state.query !== prevState.query ||
      state.sortBy !== prevState.sortBy;

    if (resultsChanged) {
      renderResults(state);
    }
  });

  // Initial render
  renderResults(store.getState());
}

// Bootstrap once DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
