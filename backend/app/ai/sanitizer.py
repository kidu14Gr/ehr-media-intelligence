"""Clinical text sanitizer.

Strips system identifiers (patient IDs, resource IDs like docref-/diagrep-, MRNs)
and raw timestamps (e.g. '2024-02-15 at 10:30:00+00:00'), converting timestamps
to plain calendar dates (YYYY-MM-DD).
"""
import re
from typing import Optional


# Regex to convert timestamps like "2024-02-15 at 10:30:00+00:00" or "2024-02-10T15:00:00Z" to "2024-02-15"
RAW_DATETIME_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})(?:T|\s+at\s+|\s+)\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b",
    re.IGNORECASE,
)

# Regex to strip standalone time expressions like "at 10:30:00+00:00" or "14:15:00+00:00"
STANDALONE_TIME_PATTERN = re.compile(
    r"\b(?:at\s+)?\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})\b",
    re.IGNORECASE,
)

# Regex to strip resource identifiers: docref-, diagrep-, enc-, doc-, bundle-, sum-
RESOURCE_ID_PATTERN = re.compile(
    r"\b(?:docref|diagrep|enc|doc|bundle|sum)-[A-Za-z0-9_-]+\b",
    re.IGNORECASE,
)

# Regex to strip surrogate patient identifiers: pat-
PATIENT_ID_PATTERN = re.compile(
    r"\bpat-[A-Za-z0-9_-]+\b",
    re.IGNORECASE,
)

# Regex to strip explicit MRN references: "MRN: 12345", "MRN-101", "MRN 10492-A"
MRN_PATTERN = re.compile(
    r"\b(?:MRN|mrn)[:\s-]*[A-Za-z0-9_-]+\b",
)


def sanitize_clinical_identifiers_and_timestamps(text: Optional[str]) -> Optional[str]:
    """Sanitizes text by stripping system IDs and simplifying raw timestamps to YYYY-MM-DD.

    Rules:
    1. Converts full datetime strings (e.g. '2024-02-15 at 10:30:00+00:00') into plain dates ('2024-02-15').
    2. Strips standalone raw time/timezone strings.
    3. Strips resource IDs (docref-*, diagrep-*, enc-*, etc.).
    4. Strips patient IDs (pat-*).
    5. Strips explicit MRN references.
    6. Normalizes resulting whitespace and cleans orphaned punctuation.
    """
    if text is None:
        return None
    if not text.strip():
        return ""

    # 1. Normalize raw timestamps with dates to YYYY-MM-DD
    cleaned = RAW_DATETIME_PATTERN.sub(r"\1", text)

    # 2. Strip remaining standalone raw timestamps
    cleaned = STANDALONE_TIME_PATTERN.sub("", cleaned)

    # 3. Strip resource IDs
    cleaned = RESOURCE_ID_PATTERN.sub("", cleaned)

    # 4. Strip patient IDs
    cleaned = PATIENT_ID_PATTERN.sub("", cleaned)

    # 5. Strip MRN references
    cleaned = MRN_PATTERN.sub("", cleaned)

    # 6. Normalize punctuation and whitespace: e.g. "( )" or " , " or multiple spaces
    cleaned = re.sub(r"\b(?:ID|id|Record ID|Resource ID)[:\s]*(?=[.,;)]|\s*$)", "", cleaned)
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r",\s*,", ", ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    return cleaned.strip()
