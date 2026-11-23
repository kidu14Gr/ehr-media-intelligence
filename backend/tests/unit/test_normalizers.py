"""Unit tests for Stage 3: Normalizers (dates, gender, MRN)."""
import pytest
from datetime import date, datetime, timezone
from app.core.exceptions import DateParsingError
from app.schemas.enums import Gender
from app.ingestion.normalizers import (
    normalize_date,
    normalize_datetime,
    normalize_gender,
    normalize_mrn,
)


def test_mrn_normalization_conservative():
    """Verify conservative MRN normalization and original preservation."""
    result = normalize_mrn("  MRN: 99402-A  ")
    assert result.value == "99402-A"
    assert result.original == "  MRN: 99402-A  "
    assert len(result.transformations) == 1
    assert "Sanitized" in result.transformations[0].reason

    clean_result = normalize_mrn("10023")
    assert clean_result.value == "10023"
    assert len(clean_result.transformations) == 0


def test_mrn_normalization_empty_raises():
    """Verify empty MRN raises ValueError."""
    with pytest.raises(ValueError, match="empty string"):
        normalize_mrn("   MRN:   ")

    with pytest.raises(ValueError, match="cannot be None"):
        normalize_mrn(None)


def test_gender_unambiguous_mappings():
    """Verify unambiguous gender values map cleanly to enum."""
    assert normalize_gender("M").value == Gender.MALE
    assert normalize_gender("male").value == Gender.MALE
    assert normalize_gender("FEMALE").value == Gender.FEMALE
    assert normalize_gender("Woman").value == Gender.FEMALE
    assert normalize_gender("non-binary").value == Gender.OTHER
    assert normalize_gender("UNKNOWN").value == Gender.UNKNOWN
    assert normalize_gender(None).value == Gender.UNKNOWN


def test_gender_ambiguous_and_numeric_values():
    """Verify ambiguous, numeric, and transgender values map to UNKNOWN with warning."""
    res_tg = normalize_gender("transgender")
    assert res_tg.value == Gender.UNKNOWN
    assert res_tg.original == "transgender"
    assert len(res_tg.warnings) == 1
    assert "Ambiguous or system-dependent" in res_tg.warnings[0]

    res_num1 = normalize_gender("1")
    assert res_num1.value == Gender.UNKNOWN
    assert res_num1.original == "1"
    assert len(res_num1.warnings) == 1

    res_num2 = normalize_gender("2")
    assert res_num2.value == Gender.UNKNOWN
    assert res_num2.original == "2"
    assert len(res_num2.warnings) == 1


def test_date_normalization_iso_direct():
    """Verify direct ISO 8601 date parsing."""
    res = normalize_date("1985-04-12")
    assert res.value == date(1985, 4, 12)
    assert not res.is_ambiguous
    assert len(res.warnings) == 0


def test_date_normalization_unambiguous_delimiter():
    """Verify delimiter-based dates where day > 12 is resolved unambiguously."""
    # 25 is day (DD/MM/YYYY)
    res_dd = normalize_date("25/04/2021")
    assert res_dd.value == date(2021, 4, 25)
    assert not res_dd.is_ambiguous

    # 25 is day (MM/DD/YYYY)
    res_mm = normalize_date("04/25/2021")
    assert res_mm.value == date(2021, 4, 25)
    assert not res_mm.is_ambiguous


def test_date_normalization_ambiguous_flagging():
    """Verify ambiguous dates (both parts <= 12) are flagged rather than silently guessed."""
    res_amb = normalize_date("03/04/2020")
    assert res_amb.value == date(2020, 3, 4)
    assert res_amb.is_ambiguous is True
    assert len(res_amb.warnings) == 1
    assert "Ambiguous date '03/04/2020' (both parts <= 12)" in res_amb.warnings[0]


def test_date_normalization_two_digit_year_ambiguous():
    """Verify two-digit-year ambiguous date (03/04/87) goes through ambiguity check."""
    res = normalize_date("03/04/87", is_dob=True)
    assert res.value == date(1987, 3, 4)
    assert res.is_ambiguous is True
    assert any("87 interpreted as 1987" in w for w in res.warnings)
    assert any("Ambiguous date '03/04/87' (both parts <= 12)" in w for w in res.warnings)


def test_date_normalization_two_digit_year_unambiguous():
    """Verify two-digit-year unambiguous dates (25/04/87 and 04/25/87) are resolved."""
    res_dd = normalize_date("25/04/87", is_dob=True)
    assert res_dd.value == date(1987, 4, 25)
    assert res_dd.is_ambiguous is False
    assert any("87 interpreted as 1987" in w for w in res_dd.warnings)

    res_mm = normalize_date("04/25/87", is_dob=True)
    assert res_mm.value == date(1987, 4, 25)
    assert res_mm.is_ambiguous is False
    assert any("87 interpreted as 1987" in w for w in res_mm.warnings)


def test_date_normalization_two_digit_year_century_expansion():
    """Verify two-digit year chooses most recent non-future century for DOB."""
    # '15' for DOB in 2026: 2015 is <= 2026, so 2015
    res_recent = normalize_date("05/20/15", is_dob=True)
    assert res_recent.value == date(2015, 5, 20)
    assert any("15 interpreted as 2015" in w for w in res_recent.warnings)

    # '95' for DOB: 2095 is in future, so 1995
    res_past = normalize_date("05/20/95", is_dob=True)
    assert res_past.value == date(1995, 5, 20)
    assert any("95 interpreted as 1995" in w for w in res_past.warnings)


def test_date_normalization_configurable_format():
    """Verify explicit preferred format overrides default parsing."""
    res = normalize_date("03/04/2020", preferred_format="%d/%m/%Y")
    assert res.value == date(2020, 4, 3)
    assert not res.is_ambiguous


def test_date_normalization_textual():
    """Verify textual date string parsing."""
    res = normalize_date("Jan 15, 2023")
    assert res.value == date(2023, 1, 15)
    assert not res.is_ambiguous


def test_date_normalization_invalid_inputs_raise():
    """Verify invalid dates and empty inputs raise DateParsingError."""
    with pytest.raises(DateParsingError, match="cannot be None"):
        normalize_date(None)

    with pytest.raises(DateParsingError, match="is empty"):
        normalize_date("   ")

    with pytest.raises(DateParsingError, match="Invalid calendar date"):
        normalize_date("2020-02-31")

    with pytest.raises(DateParsingError, match="Invalid calendar date"):
        normalize_date("31/02/2020")

    with pytest.raises(DateParsingError, match="Unable to parse date string"):
        normalize_date("completely-not-a-date")


def test_datetime_normalization_timezone_aware():
    """Verify datetime parsing always produces timezone-aware UTC datetime."""
    res_naive = normalize_datetime("2024-02-10 14:30:00")
    assert res_naive.value.tzinfo == timezone.utc
    assert res_naive.value.hour == 14

    # Timestamp with explicit offset (+02:00) should convert to UTC
    res_offset = normalize_datetime("2024-02-10T16:30:00+02:00")
    assert res_offset.value.tzinfo == timezone.utc
    assert res_offset.value.hour == 14

    # Preferred format
    res_fmt = normalize_datetime("2024/02/10 14:30", preferred_format="%Y/%m/%d %H:%M")
    assert res_fmt.value.tzinfo == timezone.utc


def test_datetime_normalization_invalid_inputs_raise():
    """Verify invalid datetimes raise DateParsingError."""
    with pytest.raises(DateParsingError, match="cannot be None"):
        normalize_datetime(None)

    with pytest.raises(DateParsingError, match="is empty"):
        normalize_datetime("")

    with pytest.raises(DateParsingError, match="Unable to parse datetime string"):
        normalize_datetime("invalid-timestamp-string")
