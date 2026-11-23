/**
 * Fetch API Client for EHR Media Intelligence Platform.
 * Supports AbortController cancellation and structured error propagation.
 * Enforces zero PHI in console logging.
 */

export class ApiError extends Error {
  constructor(message, status = 500, detail = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export class AbortedError extends Error {
  constructor(message = "Request aborted") {
    super(message);
    this.name = "AbortedError";
  }
}

/**
 * Searches clinical records by calling POST /search.
 *
 * @param {Object} params
 * @param {string} params.query
 * @param {string|null} [params.resourceType]
 * @param {string|null} [params.startDate]
 * @param {string|null} [params.endDate]
 * @param {number} [params.limit=5]
 * @param {number|null} [params.minScore]
 * @param {AbortSignal} [signal]
 * @returns {Promise<Object>} SearchResponse
 */
export async function searchRecords(
  { query, resourceType = null, startDate = null, endDate = null, limit = 5, minScore = null },
  signal = null
) {
  const payload = {
    query: query.trim(),
    limit: limit || 5,
  };
  if (resourceType && resourceType !== "All") {
    payload.resource_type = resourceType;
  }
  if (startDate) {
    payload.start_date = startDate;
  }
  if (endDate) {
    payload.end_date = endDate;
  }
  if (typeof minScore === "number") {
    payload.min_score = minScore;
  }

  try {
    const response = await fetch("/search", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify(payload),
      signal,
    });

    if (!response.ok) {
      let detailMsg = `HTTP ${response.status}`;
      try {
        const errorJson = await response.json();
        detailMsg = errorJson.detail || detailMsg;
      } catch {
        // use default status message
      }
      throw new ApiError(detailMsg, response.status, detailMsg);
    }

    const data = await response.json();
    return data;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new AbortedError();
    }
    throw err;
  }
}

/**
 * Retrieves patient profile, summary, and linked FHIR resources via GET /patients/{id}.
 *
 * @param {string} patientId
 * @param {AbortSignal} [signal]
 * @returns {Promise<Object>} PatientDetailResponse
 */
export async function getPatientDetail(patientId, signal = null) {
  try {
    const response = await fetch(`/patients/${encodeURIComponent(patientId)}`, {
      method: "GET",
      headers: {
        "Accept": "application/json",
      },
      signal,
    });

    if (!response.ok) {
      let detailMsg = `HTTP ${response.status}`;
      try {
        const errorJson = await response.json();
        detailMsg = errorJson.detail || detailMsg;
      } catch {
        // use default status
      }
      throw new ApiError(detailMsg, response.status, detailMsg);
    }

    const data = await response.json();
    return data;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new AbortedError();
    }
    throw err;
  }
}
