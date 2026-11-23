/**
 * Patient Match Card Component.
 * Displays patient demographics, raw relevance score with visual bar,
 * explicit "Undated" badges, resource-type badges, and safe term highlights.
 */

import { el, icon, highlightMatches } from "../utils/dom.js";
import { formatClinicalDate, formatScore, calculateAge, getResourceTypeConfig } from "../utils/formatters.js";

/**
 * Creates a patient search result card.
 *
 * @param {Object} result - PatientSearchResult object from POST /search
 * @param {string} currentQuery - Current search query for term highlighting
 * @param {Function} onViewDrawer - Click handler to open patient detail drawer
 * @returns {HTMLElement}
 */
export function createPatientCard(result, currentQuery, onViewDrawer) {
  const resourceCfg = getResourceTypeConfig(result.resource_type);
  const formattedScore = formatScore(result.score);
  const scorePercent = Math.min(100, Math.max(0, Math.round(Number(result.score) * 100)));
  const isUndated = !result.record_date;
  const age = calculateAge(result.date_of_birth);

  // Demographics subline
  const demoParts = [];
  demoParts.push(`MRN: ${result.mrn || "Unknown"}`);
  if (result.gender) {
    demoParts.push(result.gender.charAt(0).toUpperCase() + result.gender.slice(1));
  }
  if (result.date_of_birth) {
    const ageText = age !== null ? ` (${age}y)` : "";
    demoParts.push(`DOB: ${result.date_of_birth}${ageText}`);
  }

  const card = el(
    "article",
    {
      className:
        "group relative p-5 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-xs hover:border-teal-500/50 dark:hover:border-teal-500/50 hover:shadow-md transition-all space-y-3.5 focus-within:ring-2 focus-within:ring-blue-600 focus-within:border-transparent",
      tabIndex: 0,
      "aria-labelledby": `patient-name-${result.patient_id}`,
      onKeyDown: (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onViewDrawer(result.patient_id, card);
        }
      },
    },
    [
      // Header: Name, MRN, badges
      el("div", { className: "flex flex-wrap items-start justify-between gap-2" }, [
        el("div", {}, [
          el("div", { className: "flex items-center space-x-2.5" }, [
            el(
              "h3",
              {
                id: `patient-name-${result.patient_id}`,
                className: "text-base font-bold text-slate-900 dark:text-white tracking-tight",
              },
              result.patient_name
            ),
            el(
              "span",
              { className: "text-xs font-mono text-slate-400 dark:text-slate-500" },
              result.patient_id
            ),
          ]),
          el(
            "p",
            { className: "text-xs text-slate-500 dark:text-slate-400 font-mono mt-0.5" },
            demoParts.join(" • ")
          ),
        ]),

        // Badges: Resource Type & Record Date
        el("div", { className: "flex flex-wrap items-center gap-1.5" }, [
          // Resource Badge
          el(
            "span",
            {
              className: `inline-flex items-center space-x-1 px-2.5 py-1 rounded-md text-xs font-medium ${resourceCfg.badgeClass}`,
            },
            [icon(resourceCfg.icon, "w-3.5 h-3.5 mr-1"), resourceCfg.label]
          ),

          // Date / Undated Badge
          el(
            "span",
            {
              className: isUndated
                ? "inline-flex items-center px-2.5 py-1 rounded-md text-xs font-semibold bg-amber-50 dark:bg-amber-950/60 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-800"
                : "inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700 font-mono",
            },
            [isUndated ? "Undated" : formatClinicalDate(result.record_date)]
          ),
        ]),
      ]),

      // Score Bar & Raw Score Display (No "% match", purely raw score e.g. 0.73)
      el("div", { className: "space-y-1" }, [
        el("div", { className: "flex justify-between items-center text-xs" }, [
          el("span", { className: "text-slate-500 dark:text-slate-400 font-medium" }, "Semantic Relevance"),
          el("span", { className: "font-mono font-bold text-teal-700 dark:text-teal-400" }, `Score: ${formattedScore}`),
        ]),
        el(
          "div",
          {
            className: "w-full h-1.5 bg-slate-100 dark:bg-slate-800 rounded-full overflow-hidden",
            role: "progressbar",
            "aria-valuenow": Number(formattedScore),
            "aria-valuemin": 0,
            "aria-valuemax": 1,
            "aria-label": `Relevance score: ${formattedScore}`,
          },
          [
            el("div", {
              className: "h-full bg-teal-600 dark:bg-teal-500 rounded-full transition-all duration-300",
              style: `width: ${scorePercent}%`,
            }),
          ]
        ),
      ]),

      // AI Summary Snippet with Safe Term Highlighting
      el(
        "div",
        {
          className:
            "text-xs leading-relaxed text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/40 p-3 rounded-lg border border-slate-100 dark:border-slate-800 italic",
        },
        [highlightMatches(result.summary_snippet, currentQuery)]
      ),

      // Footer: Action link
      el("div", { className: "flex justify-end pt-1" }, [
        el(
          "button",
          {
            type: "button",
            className:
              "inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-teal-50 dark:bg-teal-950/60 text-teal-800 dark:text-teal-200 hover:bg-teal-100 dark:hover:bg-teal-900 border border-teal-200 dark:border-teal-800 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-600",
            onClick: (e) => {
              e.stopPropagation();
              onViewDrawer(result.patient_id, e.currentTarget);
            },
            "aria-label": `View complete clinical profile and summary for ${result.patient_name}`,
          },
          [
            el("span", {}, "View Patient Drawer"),
            icon("chevronRight", "w-3.5 h-3.5"),
          ]
        ),
      ]),
    ]
  );

  // Card click opens drawer
  card.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    onViewDrawer(result.patient_id, btn || card);
  });

  return card;
}
