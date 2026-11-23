/**
 * Skeletons and Empty State Renderers.
 * Accessible pulsing placeholders with role="status" and clear clinical empty states.
 */

import { el, icon } from "../utils/dom.js";

/**
 * Renders an accessible pulsing skeleton placeholder for a patient match card.
 */
export function createCardSkeleton() {
  return el(
    "div",
    {
      className:
        "p-5 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xs animate-pulse space-y-4",
      role: "status",
      "aria-label": "Loading clinical search result...",
    },
    [
      // Top row: Patient demographics & badges
      el("div", { className: "flex items-start justify-between" }, [
        el("div", { className: "space-y-2" }, [
          el("div", { className: "h-5 w-44 bg-slate-200 dark:bg-slate-800 rounded" }),
          el("div", { className: "h-3.5 w-64 bg-slate-200 dark:bg-slate-800 rounded" }),
        ]),
        el("div", { className: "flex items-center space-x-2" }, [
          el("div", { className: "h-6 w-24 bg-slate-200 dark:bg-slate-800 rounded-md" }),
          el("div", { className: "h-6 w-20 bg-slate-200 dark:bg-slate-800 rounded-md" }),
        ]),
      ]),

      // Middle: Score bar placeholder
      el("div", { className: "space-y-1.5" }, [
        el("div", { className: "h-2 w-full bg-slate-100 dark:bg-slate-800 rounded-full" }),
      ]),

      // Snippet lines
      el("div", { className: "space-y-2 pt-1" }, [
        el("div", { className: "h-3.5 w-full bg-slate-200 dark:bg-slate-800 rounded" }),
        el("div", { className: "h-3.5 w-5/6 bg-slate-200 dark:bg-slate-800 rounded" }),
      ]),

      // Action button placeholder
      el("div", { className: "flex justify-end pt-2" }, [
        el("div", { className: "h-8 w-36 bg-slate-200 dark:bg-slate-800 rounded-lg" }),
      ]),
    ]
  );
}

/**
 * Initial empty state when user hasn't searched yet.
 */
export function createInitialEmptyState(onQueryClick) {
  const container = el(
    "div",
    {
      className:
        "py-16 px-4 text-center max-w-lg mx-auto bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-xs space-y-4",
    },
    [
      el(
        "div",
        {
          className:
            "w-12 h-12 mx-auto rounded-full bg-teal-50 dark:bg-teal-950/60 text-teal-700 dark:text-teal-300 flex items-center justify-center",
        },
        [icon("search", "w-6 h-6")]
      ),
      el("h3", { className: "text-base font-semibold text-slate-900 dark:text-white" }, "Search Clinical Records"),
      el(
        "p",
        { className: "text-xs text-slate-500 dark:text-slate-400 leading-relaxed" },
        "Enter a free-text clinical query above to semantically search physician notes, diagnostic imaging reports, and synthesized patient summaries."
      ),
      el("div", { className: "pt-2 flex flex-wrap justify-center gap-2" }, [
        el("span", { className: "text-xs text-slate-400 dark:text-slate-500 self-center" }, "Try:"),
        el(
          "button",
          {
            type: "button",
            className:
              "text-xs px-2.5 py-1 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-teal-50 hover:text-teal-700 transition-colors",
            onClick: () => onQueryClick("lumbar spine MRI"),
          },
          "lumbar spine MRI"
        ),
        el(
          "button",
          {
            type: "button",
            className:
              "text-xs px-2.5 py-1 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-teal-50 hover:text-teal-700 transition-colors",
            onClick: () => onQueryClick("acute non-cardiac chest pain"),
          },
          "chest pain"
        ),
        el(
          "button",
          {
            type: "button",
            className:
              "text-xs px-2.5 py-1 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-teal-50 hover:text-teal-700 transition-colors",
            onClick: () => onQueryClick("chronic migraine"),
          },
          "migraine"
        ),
      ]),
    ]
  );
  return container;
}

/**
 * No results state when query produces 0 matches.
 */
export function createNoResultsState(query, onResetFilters) {
  return el(
    "div",
    {
      className:
        "py-12 px-4 text-center max-w-md mx-auto bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-xs space-y-3",
    },
    [
      el(
        "div",
        {
          className:
            "w-10 h-10 mx-auto rounded-full bg-amber-50 dark:bg-amber-950/60 text-amber-700 dark:text-amber-300 flex items-center justify-center",
        },
        [icon("alertCircle", "w-5 h-5")]
      ),
      el("h3", { className: "text-sm font-semibold text-slate-900 dark:text-white" }, "No Clinical Matches Found"),
      el(
        "p",
        { className: "text-xs text-slate-500 dark:text-slate-400 leading-relaxed" },
        `No records in the cohort matched "${query}" under current filter criteria.`
      ),
      onResetFilters
        ? el(
            "button",
            {
              type: "button",
              className:
                "mt-2 text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200 hover:bg-slate-200 transition-colors",
              onClick: onResetFilters,
            },
            "Reset Filter Criteria"
          )
        : null,
    ]
  );
}
