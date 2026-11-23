/**
 * Clinical Data Formatters.
 * Formats dates, raw relevance scores, ages, and semantic badges.
 */

/**
 * Formats an ISO date into a readable string or returns "Undated".
 */
export function formatClinicalDate(isoDate) {
  if (!isoDate) return "Undated";
  try {
    const d = new Date(isoDate);
    if (isNaN(d.getTime())) return "Undated";
    return d.toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  } catch {
    return "Undated";
  }
}

/**
 * Calculates patient age given a date of birth.
 */
export function calculateAge(dob) {
  if (!dob) return null;
  try {
    const birth = new Date(dob);
    if (isNaN(birth.getTime())) return null;
    const now = new Date();
    let age = now.getFullYear() - birth.getFullYear();
    const m = now.getMonth() - birth.getMonth();
    if (m < 0 || (m === 0 && now.getDate() < birth.getDate())) {
      age--;
    }
    return age >= 0 ? age : null;
  } catch {
    return null;
  }
}

/**
 * Formats raw relevance score to 2 decimal places (e.g. 0.73).
 * Explicitly satisfies requirement: "Show relevance as the raw score (e.g. 0.73) with a bar.
 * No '% match' labels and no High/Moderate labels unless based on documented thresholds."
 */
export function formatScore(score) {
  const num = Number(score);
  if (isNaN(num)) return "0.00";
  return num.toFixed(2);
}

/**
 * Returns Tailwind classes and display title for FHIR resource types.
 */
export function getResourceTypeConfig(resourceType) {
  switch (resourceType) {
    case "DocumentReference":
      return {
        label: "Clinical Note",
        icon: "fileText",
        badgeClass: "bg-blue-50 dark:bg-blue-950/60 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800",
      };
    case "DiagnosticReport":
      return {
        label: "Diagnostic Report",
        icon: "activity",
        badgeClass: "bg-purple-50 dark:bg-purple-950/60 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800",
      };
    case "Summary":
      return {
        label: "AI Summary",
        icon: "sparkles",
        badgeClass: "bg-teal-50 dark:bg-teal-950/60 text-teal-700 dark:text-teal-300 border border-teal-200 dark:border-teal-800",
      };
    case "Patient":
      return {
        label: "Patient Demographic",
        icon: "user",
        badgeClass: "bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700",
      };
    case "Encounter":
      return {
        label: "Encounter",
        icon: "calendar",
        badgeClass: "bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800",
      };
    default:
      return {
        label: resourceType || "Resource",
        icon: "fileText",
        badgeClass: "bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700",
      };
  }
}

/**
 * Returns styling for clinical anomaly severity.
 */
export function getSeverityConfig(severity) {
  const s = (severity || "unknown").toLowerCase();
  switch (s) {
    case "critical":
      return {
        label: "Critical Alert",
        icon: "alertTriangle",
        badgeClass: "bg-rose-50 dark:bg-rose-950/60 text-rose-800 dark:text-rose-200 border border-rose-300 dark:border-rose-800 font-semibold",
      };
    case "high":
      return {
        label: "High Severity",
        icon: "alertTriangle",
        badgeClass: "bg-orange-50 dark:bg-orange-950/60 text-orange-800 dark:text-orange-200 border border-orange-300 dark:border-orange-800",
      };
    case "moderate":
      return {
        label: "Moderate Alert",
        icon: "alertCircle",
        badgeClass: "bg-amber-50 dark:bg-amber-950/60 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-800",
      };
    case "low":
      return {
        label: "Low Severity",
        icon: "alertCircle",
        badgeClass: "bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700",
      };
    default:
      return {
        label: "Finding",
        icon: "alertCircle",
        badgeClass: "bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-700",
      };
  }
}
