"""Clinical data normalizers for dates, demographics, and medical record numbers.

Adheres strictly to the architectural requirements:
- Composite patient identity (facility_id, MRN).
- Configurable date parsing with explicit flagging of ambiguous dates (no silent guessing).
- Strict handling of two-digit years: ambiguity check, expansion warning, most recent non-future century for DOB.
- Unambiguous gender normalization (ambiguous/numeric values map to UNKNOWN with audit warning).
- Conservative MRN normalization preserving original values.
- Timezone-aware UTC timestamps.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import re
from typing import List, Optional
from dateutil import parser as dateutil_parser

from app.core.exceptions import DateParsingError
from app.schemas.enums import Gender
from app.schemas.audit import TransformationRecord


@dataclass(frozen=True)
class DateNormalizationResult:
    """Result of date normalization with audit tracking."""
    value: Optional[date]
    original: str
    transformations: List[TransformationRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    is_ambiguous: bool = False


@dataclass(frozen=True)
class DateTimeNormalizationResult:
    """Result of clinical timestamp normalization with timezone enforcement."""
    value: Optional[datetime]
    original: str
    transformations: List[TransformationRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GenderNormalizationResult:
    """Result of gender normalization with audit tracking."""
    value: Gender
    original: Optional[str]
    transformations: List[TransformationRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MRNNormalizationResult:
    """Result of conservative MRN sanitization."""
    value: str
    original: Optional[str]
    transformations: List[TransformationRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# Unambiguous gender dictionary (exact lowercase match)
UNAMBIGUOUS_GENDER_MAP = {
    "m": Gender.MALE,
    "male": Gender.MALE,
    "man": Gender.MALE,
    "boy": Gender.MALE,
    "f": Gender.FEMALE,
    "female": Gender.FEMALE,
    "woman": Gender.FEMALE,
    "girl": Gender.FEMALE,
    "o": Gender.OTHER,
    "other": Gender.OTHER,
    "non-binary": Gender.OTHER,
    "nonbinary": Gender.OTHER,
    "nb": Gender.OTHER,
    "u": Gender.UNKNOWN,
    "unknown": Gender.UNKNOWN,
    "unk": Gender.UNKNOWN,
}


def normalize_mrn(raw_mrn: Optional[str]) -> MRNNormalizationResult:
    """Conservatively normalizes MRN while preserving original value.
    
    - Strips leading/trailing whitespace.
    - Strips common label prefixes like 'MRN:', 'MRN#'.
    - Preserves internal letters, dashes, and numbers.
    """
    if raw_mrn is None:
        raise ValueError("MRN cannot be None")

    original = str(raw_mrn)
    cleaned = original.strip()

    # Conservatively strip common prefixes like "MRN:", "MRN #"
    prefix_match = re.match(r"^MRN\s*[:#\-]?\s*", cleaned, re.IGNORECASE)
    if prefix_match:
        cleaned = cleaned[prefix_match.end():].strip()

    if not cleaned:
        raise ValueError(f"MRN '{raw_mrn}' resolves to empty string after sanitization")

    transformations: List[TransformationRecord] = []
    if cleaned != original:
        transformations.append(
            TransformationRecord(
                field_name="mrn",
                original_value=original,
                normalized_value=cleaned,
                reason="Sanitized leading/trailing whitespace and redundant label prefix",
            )
        )

    return MRNNormalizationResult(
        value=cleaned,
        original=original,
        transformations=transformations,
        warnings=[],
    )


def normalize_gender(raw_gender: Optional[str]) -> GenderNormalizationResult:
    """Normalizes gender string using strictly unambiguous mappings.
    
    Any ambiguous string (including 'transgender', numeric codes '1'/'2' which differ
    between systems, or unrecognized strings) maps to Gender.UNKNOWN, preserves
    original_gender, and generates an audit warning.
    """
    if raw_gender is None:
        return GenderNormalizationResult(
            value=Gender.UNKNOWN,
            original=None,
            transformations=[],
            warnings=[],
        )

    original = str(raw_gender).strip()
    lookup_key = original.lower()

    if lookup_key in UNAMBIGUOUS_GENDER_MAP:
        canonical = UNAMBIGUOUS_GENDER_MAP[lookup_key]
        transformations: List[TransformationRecord] = []
        if original != canonical.value:
            transformations.append(
                TransformationRecord(
                    field_name="gender",
                    original_value=original,
                    normalized_value=canonical.value,
                    reason="Normalized from unambiguous clinical gender variant",
                )
            )
        return GenderNormalizationResult(
            value=canonical,
            original=original,
            transformations=transformations,
            warnings=[],
        )

    # Ambiguous or non-standard gender representation
    warning_msg = (
        f"Ambiguous or system-dependent gender '{original}' normalized to UNKNOWN; "
        "original value preserved."
    )
    transformation = TransformationRecord(
        field_name="gender",
        original_value=original,
        normalized_value=Gender.UNKNOWN.value,
        reason="Ambiguous or non-standard representation; preserved in original_gender",
    )
    return GenderNormalizationResult(
        value=Gender.UNKNOWN,
        original=original,
        transformations=[transformation],
        warnings=[warning_msg],
    )


def _expand_two_digit_year(year_2d: int, is_dob: bool) -> tuple[int, str]:
    """Expands a two-digit year into a 4-digit year with audit warning.
    
    For DOB fields, chooses the most recent century that is not in the future.
    """
    current_year = date.today().year
    century_2000 = 2000 + year_2d
    
    if is_dob:
        if century_2000 <= current_year:
            year_4d = century_2000
        else:
            year_4d = 1900 + year_2d
    else:
        if century_2000 <= (current_year + 1):
            year_4d = century_2000
        else:
            year_4d = 1900 + year_2d

    warning = f"{year_2d:02d} interpreted as {year_4d}"
    return year_4d, warning


def normalize_date(
    raw_date: Optional[str],
    preferred_format: Optional[str] = None,
    is_dob: bool = False,
) -> DateNormalizationResult:
    """Parses and normalizes dates into ISO 8601 (YYYY-MM-DD).
    
    Supports configurable date formats. Flags ambiguous dates (e.g., '03/04/2020' or
    '03/04/87') when format is unspecified rather than silently guessing day vs. month.
    Two-digit years are expanded with explicit warnings and century resolution.
    """
    if raw_date is None:
        raise DateParsingError("Date string cannot be None")

    original = str(raw_date).strip()
    if not original:
        raise DateParsingError("Date string is empty")

    transformations: List[TransformationRecord] = []
    warnings: List[str] = []

    # 1. Direct ISO 8601 matching: YYYY-MM-DD
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", original)
    if iso_match:
        try:
            year, month, day = map(int, iso_match.groups())
            parsed_date = date(year, month, day)
            return DateNormalizationResult(
                value=parsed_date,
                original=original,
                transformations=[],
                warnings=[],
                is_ambiguous=False,
            )
        except ValueError as e:
            raise DateParsingError(f"Invalid calendar date '{original}': {e}") from e

    # 2. Configurable explicit format provided
    if preferred_format:
        try:
            parsed_dt = datetime.strptime(original, preferred_format)
            parsed_date = parsed_dt.date()
            transformations.append(
                TransformationRecord(
                    field_name="date",
                    original_value=original,
                    normalized_value=parsed_date.isoformat(),
                    reason=f"Parsed using explicit preferred format '{preferred_format}'",
                )
            )
            return DateNormalizationResult(
                value=parsed_date,
                original=original,
                transformations=transformations,
                warnings=[],
                is_ambiguous=False,
            )
        except ValueError as e:
            warnings.append(
                f"Failed to parse '{original}' using preferred format '{preferred_format}': {e}"
            )

    # 3. Numeric delimiter patterns with 4-digit or 2-digit years
    # Group 1: p1, Group 2: p2, Group 3: year (2 or 4 digits)
    delim_match = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$", original)
    if delim_match:
        p1, p2, year_str = delim_match.groups()
        p1, p2 = int(p1), int(p2)

        if len(year_str) == 2:
            year_4d, year_warning = _expand_two_digit_year(int(year_str), is_dob=is_dob)
            warnings.append(year_warning)
            transformations.append(
                TransformationRecord(
                    field_name="date",
                    original_value=year_str,
                    normalized_value=str(year_4d),
                    reason=f"Expanded two-digit year '{year_str}' to '{year_4d}'",
                )
            )
        else:
            year_4d = int(year_str)

        # Check for unambiguous day/month boundary (>12 must be day)
        if p1 > 12 and p2 <= 12:
            # Unambiguously Day/Month/Year (e.g. 25/04/87 or 25/04/2021)
            try:
                parsed_date = date(year_4d, p2, p1)
            except ValueError as e:
                raise DateParsingError(f"Invalid calendar date '{original}': {e}") from e

            transformations.append(
                TransformationRecord(
                    field_name="date",
                    original_value=original,
                    normalized_value=parsed_date.isoformat(),
                    reason="Normalized unambiguous DD/MM/YYYY date",
                )
            )
            return DateNormalizationResult(
                value=parsed_date,
                original=original,
                transformations=transformations,
                warnings=warnings,
                is_ambiguous=False,
            )
        elif p2 > 12 and p1 <= 12:
            # Unambiguously Month/Day/Year (e.g. 04/25/87 or 04/25/2021)
            try:
                parsed_date = date(year_4d, p1, p2)
            except ValueError as e:
                raise DateParsingError(f"Invalid calendar date '{original}': {e}") from e

            transformations.append(
                TransformationRecord(
                    field_name="date",
                    original_value=original,
                    normalized_value=parsed_date.isoformat(),
                    reason="Normalized unambiguous MM/DD/YYYY date",
                )
            )
            return DateNormalizationResult(
                value=parsed_date,
                original=original,
                transformations=transformations,
                warnings=warnings,
                is_ambiguous=False,
            )
        elif p1 <= 12 and p2 <= 12:
            # Both p1 and p2 <= 12: AMBIGUOUS! (e.g. 03/04/87 or 03/04/2020)
            try:
                parsed_date = date(year_4d, p1, p2)
            except ValueError as e:
                raise DateParsingError(f"Invalid calendar date '{original}': {e}") from e

            warning_msg = (
                f"Ambiguous date '{original}' (both parts <= 12). Defaulted to MM/DD/YYYY "
                f"({parsed_date.isoformat()}), but flagged for clinical verification."
            )
            transformations.append(
                TransformationRecord(
                    field_name="date",
                    original_value=original,
                    normalized_value=parsed_date.isoformat(),
                    reason="Parsed ambiguous date using default MM/DD/YYYY convention; flagged",
                )
            )
            warnings.append(warning_msg)
            return DateNormalizationResult(
                value=parsed_date,
                original=original,
                transformations=transformations,
                warnings=warnings,
                is_ambiguous=True,
            )
        else:
            raise DateParsingError(f"Invalid date components in '{original}': {p1}, {p2}")

    # 4. Textual and complex date parsing using dateutil
    try:
        parsed_dt = dateutil_parser.parse(original, fuzzy=False)
        parsed_date = parsed_dt.date()
        transformations.append(
            TransformationRecord(
                field_name="date",
                original_value=original,
                normalized_value=parsed_date.isoformat(),
                reason="Normalized textual date format",
            )
        )
        return DateNormalizationResult(
            value=parsed_date,
            original=original,
            transformations=transformations,
            warnings=warnings,
            is_ambiguous=False,
        )
    except Exception as e:
        raise DateParsingError(f"Unable to parse date string '{original}': {e}") from e


def normalize_datetime(
    raw_dt: Optional[str],
    preferred_format: Optional[str] = None,
) -> DateTimeNormalizationResult:
    """Parses and normalizes clinical timestamps into timezone-aware UTC datetime."""
    if raw_dt is None:
        raise DateParsingError("Datetime string cannot be None")

    original = str(raw_dt).strip()
    if not original:
        raise DateParsingError("Datetime string is empty")

    transformations: List[TransformationRecord] = []
    warnings: List[str] = []

    if preferred_format:
        try:
            dt = datetime.strptime(original, preferred_format)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)

            transformations.append(
                TransformationRecord(
                    field_name="effective_date",
                    original_value=original,
                    normalized_value=dt.isoformat(),
                    reason=f"Parsed timestamp using preferred format '{preferred_format}'",
                )
            )
            return DateTimeNormalizationResult(
                value=dt,
                original=original,
                transformations=transformations,
                warnings=warnings,
            )
        except ValueError as e:
            warnings.append(
                f"Failed to parse datetime '{original}' using format '{preferred_format}': {e}"
            )

    try:
        dt = dateutil_parser.parse(original)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            transformations.append(
                TransformationRecord(
                    field_name="effective_date",
                    original_value=original,
                    normalized_value=dt.isoformat(),
                    reason="Assigned UTC timezone to naive timestamp",
                )
            )
        else:
            utc_dt = dt.astimezone(timezone.utc)
            if utc_dt != dt or original != utc_dt.isoformat():
                transformations.append(
                    TransformationRecord(
                        field_name="effective_date",
                        original_value=original,
                        normalized_value=utc_dt.isoformat(),
                        reason="Converted timestamp to UTC timezone",
                    )
                )
            dt = utc_dt

        return DateTimeNormalizationResult(
            value=dt,
            original=original,
            transformations=transformations,
            warnings=warnings,
        )
    except Exception as e:
        raise DateParsingError(f"Unable to parse datetime string '{original}': {e}") from e
