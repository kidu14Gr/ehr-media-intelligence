/**
 * Patient Clinical Profile Slide-Over Drawer Component.
 * Presents comprehensive AI summary, facts vs interpretation split,
 * unverified diagnoses isolation, and linked FHIR resource inspector.
 */

import { el, icon, clearChildren } from "../utils/dom.js";
import { formatClinicalDate, calculateAge, getResourceTypeConfig, getSeverityConfig } from "../utils/formatters.js";
import { trapFocus, releaseFocusTrap } from "../utils/a11y.js";

/**
 * Displays a toast notification in bottom right corner.
 */
export function showToast(message, type = "success") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = el(
    "div",
    {
      className:
        "pointer-events-auto flex items-center space-x-2 px-4 py-2.5 rounded-lg shadow-lg text-xs font-semibold bg-slate-900 text-white dark:bg-white dark:text-slate-900 transition-all duration-300 transform translate-y-2 opacity-0",
    },
    [
      icon(type === "success" ? "check" : "alertCircle", "w-4 h-4 text-teal-400 dark:text-teal-600"),
      el("span", {}, message),
    ]
  );

  container.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.remove("translate-y-2", "opacity-0");
  });

  setTimeout(() => {
    toast.classList.add("translate-y-2", "opacity-0");
    setTimeout(() => {
      if (toast.parentElement) toast.parentElement.removeChild(toast);
    }, 300);
  }, 2500);
}

/**
 * Binds drawer event handlers and tab switching.
 */
export function initPatientDrawer({ onClose }) {
  const container = document.getElementById("drawer-container");
  const backdrop = document.getElementById("drawer-backdrop");
  const panel = document.getElementById("patient-drawer");
  const closeBtn = document.getElementById("drawer-close-btn");
  const footerCloseBtn = document.getElementById("drawer-footer-close-btn");
  const copyBtn = document.getElementById("drawer-copy-summary-btn");
  const printBtn = document.getElementById("drawer-print-btn");

  const tabBtnSummary = document.getElementById("tab-btn-summary");
  const tabBtnFhir = document.getElementById("tab-btn-fhir");
  const tabPaneSummary = document.getElementById("tab-pane-summary");
  const tabPaneFhir = document.getElementById("tab-pane-fhir");

  // Close handlers
  const close = () => {
    panel.classList.add("translate-x-full");
    backdrop.classList.add("opacity-0");
    releaseFocusTrap();
    setTimeout(() => {
      container.classList.add("hidden");
      container.setAttribute("aria-hidden", "true");
      if (typeof onClose === "function") onClose();
    }, 300);
  };

  closeBtn.addEventListener("click", close);
  footerCloseBtn.addEventListener("click", close);
  backdrop.addEventListener("click", close);

  // Tab switching
  tabBtnSummary.addEventListener("click", () => {
    tabBtnSummary.setAttribute("aria-selected", "true");
    tabBtnSummary.className = "py-3 border-b-2 border-teal-700 dark:border-teal-400 font-semibold text-teal-800 dark:text-teal-300 focus:outline-none cursor-pointer";
    tabBtnFhir.setAttribute("aria-selected", "false");
    tabBtnFhir.className = "py-3 border-b-2 border-transparent font-medium text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200 focus:outline-none cursor-pointer";
    tabPaneSummary.classList.remove("hidden");
    tabPaneFhir.classList.add("hidden");
  });

  tabBtnFhir.addEventListener("click", () => {
    tabBtnFhir.setAttribute("aria-selected", "true");
    tabBtnFhir.className = "py-3 border-b-2 border-teal-700 dark:border-teal-400 font-semibold text-teal-800 dark:text-teal-300 focus:outline-none cursor-pointer";
    tabBtnSummary.setAttribute("aria-selected", "false");
    tabBtnSummary.className = "py-3 border-b-2 border-transparent font-medium text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200 focus:outline-none cursor-pointer";
    tabPaneFhir.classList.remove("hidden");
    tabPaneSummary.classList.add("hidden");
  });

  // Print button
  printBtn.addEventListener("click", () => {
    window.print();
  });

  return { close };
}

/**
 * Populates and reveals the patient drawer with data from GET /patients/{id}.
 */
export function openPatientDrawer(detail, triggerElement = null) {
  const container = document.getElementById("drawer-container");
  const backdrop = document.getElementById("drawer-backdrop");
  const panel = document.getElementById("patient-drawer");
  const copyBtn = document.getElementById("drawer-copy-summary-btn");

  container.classList.remove("hidden");
  container.setAttribute("aria-hidden", "false");

  // Animate slide-in
  requestAnimationFrame(() => {
    backdrop.classList.remove("opacity-0");
    panel.classList.remove("translate-x-full");
    trapFocus(panel, "#drawer-close-btn", triggerElement);
  });

  const p = detail.patient || {};
  const s = detail.summary || {};
  const linked = detail.linked_resources || [];

  // Demographics
  document.getElementById("drawer-patient-name").textContent = `${p.first_name || ""} ${p.last_name || ""}`.trim() || p.patient_id;
  document.getElementById("drawer-mrn").textContent = p.mrn || "Unknown";
  document.getElementById("drawer-pat-id").textContent = p.patient_id || "--";

  const age = calculateAge(p.date_of_birth);
  const dobText = p.date_of_birth ? `${p.date_of_birth}${age !== null ? ` (${age}y)` : ""}` : "Not documented";
  document.getElementById("drawer-dob").textContent = dobText;

  const genderBadge = document.getElementById("drawer-gender-badge");
  genderBadge.textContent = p.gender ? p.gender.toUpperCase() : "UNKNOWN";

  // Chief Concern
  const chiefConcernEl = document.getElementById("drawer-chief-concern");
  chiefConcernEl.textContent = s.chief_concern ? s.chief_concern : "Not documented";

  // Documented Diagnoses
  const docDiagList = document.getElementById("drawer-documented-diagnoses-list");
  clearChildren(docDiagList);
  const docDiags = s.documented_diagnoses || [];
  if (docDiags.length === 0) {
    docDiagList.appendChild(el("span", { className: "text-xs text-slate-400 italic" }, "None documented"));
  } else {
    for (const d of docDiags) {
      docDiagList.appendChild(
        el(
          "span",
          {
            className:
              "inline-flex items-center space-x-1 px-2.5 py-1 rounded-md text-xs font-semibold bg-emerald-50 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800",
          },
          [icon("checkCircle", "w-3 h-3 mr-1"), d]
        )
      );
    }
  }

  // Unverified Diagnoses (ONLY renders if list is non-empty)
  const unverifiedBox = document.getElementById("drawer-unverified-box");
  const unverifiedList = document.getElementById("drawer-unverified-diagnoses-list");
  clearChildren(unverifiedList);
  const unverified = s.unverified_or_flagged_diagnoses || [];
  if (unverified.length > 0) {
    unverifiedBox.classList.remove("hidden");
    for (const d of unverified) {
      unverifiedList.appendChild(
        el(
          "span",
          {
            className:
              "inline-flex items-center space-x-1 px-2.5 py-1 rounded-md text-xs font-semibold bg-amber-100 dark:bg-amber-900/60 text-amber-900 dark:text-amber-200 border border-amber-300 dark:border-amber-700",
          },
          [icon("alertTriangle", "w-3 h-3 mr-1"), d]
        )
      );
    }
  } else {
    unverifiedBox.classList.add("hidden");
  }

  // Key Findings
  const findingsList = document.getElementById("drawer-key-findings-list");
  clearChildren(findingsList);
  const findings = s.key_findings || [];
  if (findings.length === 0) {
    findingsList.appendChild(el("li", { className: "text-slate-400 italic list-none" }, "No key laboratory or clinical findings reported."));
  } else {
    for (const f of findings) {
      findingsList.appendChild(el("li", {}, f));
    }
  }

  // Anomalies
  const anomaliesContainer = document.getElementById("drawer-anomalies-list");
  clearChildren(anomaliesContainer);
  const anomalies = s.notable_anomalies || [];
  if (anomalies.length === 0) {
    anomaliesContainer.appendChild(el("p", { className: "text-xs text-slate-400 italic" }, "No abnormal clinical anomalies detected."));
  } else {
    for (const anom of anomalies) {
      const sevCfg = getSeverityConfig(anom.severity);
      anomaliesContainer.appendChild(
        el(
          "div",
          {
            className: `p-3 rounded-lg border ${sevCfg.badgeClass} flex items-start space-x-2.5`,
          },
          [
            icon(sevCfg.icon, "w-4 h-4 shrink-0 mt-0.5"),
            el("div", { className: "text-xs leading-relaxed" }, [
              el("span", { className: "font-bold uppercase tracking-wider mr-1.5" }, `[${sevCfg.label}]:`),
              anom.description,
            ]),
          ]
        )
      );
    }
  }

  // Media
  const mediaContainer = document.getElementById("drawer-media-list");
  clearChildren(mediaContainer);
  const mediaItems = s.recent_medical_media || [];
  if (mediaItems.length === 0) {
    mediaContainer.appendChild(el("p", { className: "text-xs text-slate-400 italic" }, "No imaging studies or diagnostic media recorded."));
  } else {
    for (const m of mediaItems) {
      mediaContainer.appendChild(
        el(
          "div",
          {
            className: "p-3 rounded-lg bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-800 text-xs space-y-1",
          },
          [
            el("div", { className: "flex justify-between font-semibold text-slate-800 dark:text-slate-200" }, [
              el("span", {}, m.modality || "Medical Media"),
              el("span", { className: "font-mono text-slate-400" }, m.date || "Undated"),
            ]),
            m.description ? el("p", { className: "text-slate-600 dark:text-slate-400" }, m.description) : null,
            m.findings ? el("p", { className: "text-slate-700 dark:text-slate-300 font-medium" }, `Findings: ${m.findings}`) : null,
          ]
        )
      );
    }
  }

  // Narrative Summary (Fact vs Interpretation Split)
  const narrativeContainer = document.getElementById("drawer-narrative-container");
  clearChildren(narrativeContainer);
  const rawNarrative = s.narrative_summary || "No narrative summary generated.";
  document.getElementById("drawer-word-count-badge").textContent = `${s.word_count || rawNarrative.split(/\s+/).length} words`;

  if (rawNarrative.includes("Interpretation:")) {
    const parts = rawNarrative.split("Interpretation:");
    const factualPart = parts[0].trim();
    const interpPart = parts[1].trim();

    if (factualPart) {
      narrativeContainer.appendChild(el("p", { className: "leading-relaxed" }, factualPart));
    }
    if (interpPart) {
      narrativeContainer.appendChild(
        el(
          "div",
          {
            className: "mt-2 p-3 rounded-lg bg-indigo-50/80 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800 text-indigo-950 dark:text-indigo-200 text-xs",
          },
          [
            el("div", { className: "flex items-center space-x-1 font-bold text-indigo-800 dark:text-indigo-300 mb-1" }, [
              icon("sparkles", "w-3.5 h-3.5 text-indigo-600"),
              el("span", {}, "AI Clinical Interpretation & Synthesis:"),
            ]),
            el("p", { className: "leading-relaxed italic" }, interpPart),
          ]
        )
      );
    }
  } else {
    narrativeContainer.appendChild(el("p", { className: "leading-relaxed" }, rawNarrative));
  }

  // Copy Summary Handler
  copyBtn.onclick = () => {
    const summaryText = `PATIENT CLINICAL SUMMARY: ${p.first_name} ${p.last_name} (MRN: ${p.mrn})
CHIEF CONCERN: ${s.chief_concern || "Not documented"}
DOCUMENTED DIAGNOSES: ${(s.documented_diagnoses || []).join(", ") || "None"}
NARRATIVE: ${s.narrative_summary || ""}
DISCLAIMER: ${s.disclaimer || ""}`;
    navigator.clipboard.writeText(summaryText).then(() => {
      showToast("Clinical summary copied to clipboard");
    });
  };

  // Tab 2: Linked FHIR Resources
  document.getElementById("drawer-fhir-count").textContent = String(linked.length);
  const fhirContainer = document.getElementById("drawer-fhir-resources-list");
  clearChildren(fhirContainer);

  if (linked.length === 0) {
    fhirContainer.appendChild(el("p", { className: "text-xs text-slate-400 italic" }, "No linked FHIR resources found."));
  } else {
    for (const res of linked) {
      const rCfg = getResourceTypeConfig(res.resource_type);
      
      // UI Fix 7: Show "n/a" instead of "Undated" for Patient and Encounter resources
      let dateDisplay;
      if (res.resource_type === "Patient" || res.resource_type === "Encounter") {
        dateDisplay = "n/a";
      } else {
        dateDisplay = res.effective_date ? formatClinicalDate(res.effective_date) : "Undated";
      }

      const fhirCard = el(
        "div",
        {
          className: "p-4 rounded-xl bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-800 space-y-2.5",
        },
        [
          // Header
          el("div", { className: "flex items-start justify-between" }, [
            el("div", { className: "space-y-0.5" }, [
              el("div", { className: "flex items-center space-x-2" }, [
                el("span", { className: `px-2 py-0.5 rounded text-xs font-semibold ${rCfg.badgeClass}` }, res.resource_type),
                el("span", { className: "font-mono font-bold text-xs text-slate-800 dark:text-slate-200" }, res.resource_id),
              ]),
              res.encounter_id
                ? el("p", { className: "text-[11px] font-mono text-slate-500" }, `Encounter: ${res.encounter_id}`)
                : null,
            ]),
            el(
              "span",
              {
                className: `text-xs font-mono ${
                  dateDisplay === "n/a"
                    ? "text-slate-400 dark:text-slate-500 italic"
                    : dateDisplay === "Undated"
                    ? "text-amber-700 dark:text-amber-400 font-semibold"
                    : "text-slate-500 dark:text-slate-400"
                }`,
              },
              dateDisplay
            ),
          ]),

          // Collapsible JSON Inspector
          el("details", { className: "group text-xs" }, [
            el(
              "summary",
              {
                className: "cursor-pointer font-semibold text-teal-700 dark:text-teal-400 hover:text-teal-800 py-1 focus:outline-none flex items-center space-x-1 select-none",
              },
              [
                icon("fileText", "w-3.5 h-3.5"),
                el("span", {}, "View Raw FHIR JSON"),
              ]
            ),
            el("div", { className: "mt-2 relative" }, [
              el(
                "button",
                {
                  type: "button",
                  className: "absolute top-2 right-2 px-2 py-1 bg-slate-200 dark:bg-slate-700 hover:bg-slate-300 rounded text-[11px] font-mono text-slate-700 dark:text-slate-200",
                  onClick: () => {
                    navigator.clipboard.writeText(JSON.stringify(res.fhir_json, null, 2)).then(() => {
                      showToast(`Copied ${res.resource_type} JSON to clipboard`);
                    });
                  },
                },
                "Copy JSON"
              ),
              el(
                "pre",
                {
                  className: "p-3 rounded-lg bg-slate-900 text-slate-100 font-mono text-[11px] overflow-x-auto max-h-60 leading-normal",
                },
                JSON.stringify(res.fhir_json, null, 2)
              ),
            ]),
          ]),
        ]
      );

      fhirContainer.appendChild(fhirCard);
    }
  }
}
