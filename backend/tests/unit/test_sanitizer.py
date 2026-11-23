"""Unit tests for clinical text sanitizer (Task 3 & 4 quality requirements).

Verifies that system identifiers (pat-*, docref-*, diagrep-*, enc-*, MRN-*) and raw timestamps
(e.g. 2024-02-15 at 10:30:00+00:00) are stripped or simplified to plain calendar dates (YYYY-MM-DD).
"""
import pytest
from app.ai.sanitizer import sanitize_clinical_identifiers_and_timestamps


def test_sanitize_raw_timestamps_to_plain_dates():
    # Full ISO timestamp with 'T' and timezone
    text1 = "Patient visited clinic on 2024-02-15T10:30:00Z for evaluation."
    assert sanitize_clinical_identifiers_and_timestamps(text1) == "Patient visited clinic on 2024-02-15 for evaluation."

    # Full timestamp with 'at' and timezone offset
    text2 = "Procedure completed on 2023-11-04 at 14:15:00+00:00 without complication."
    assert sanitize_clinical_identifiers_and_timestamps(text2) == "Procedure completed on 2023-11-04 without complication."

    # Date with time without 'at'
    text3 = "Encounter recorded on 2022-08-19 09:00:00-05:00 in outpatient setting."
    assert sanitize_clinical_identifiers_and_timestamps(text3) == "Encounter recorded on 2022-08-19 in outpatient setting."


def test_sanitize_standalone_time_strings():
    text = "Follow-up was scheduled at 10:30:00+00:00 for the afternoon."
    cleaned = sanitize_clinical_identifiers_and_timestamps(text)
    assert "10:30:00" not in cleaned
    assert "+00:00" not in cleaned


def test_sanitize_resource_identifiers():
    text = "Findings documented in docref-DOC-703F2978FE and diagrep-DOC-A354972746 under enc-ENC-99213."
    cleaned = sanitize_clinical_identifiers_and_timestamps(text)
    assert "docref-" not in cleaned
    assert "diagrep-" not in cleaned
    assert "enc-" not in cleaned


def test_sanitize_patient_and_mrn_identifiers():
    text = "Summary for pat-REC-3703B61FE28A (MRN: 10492-A), with prior record MRN-99812."
    cleaned = sanitize_clinical_identifiers_and_timestamps(text)
    assert "pat-" not in cleaned
    assert "3703B61FE28A" not in cleaned
    assert "MRN" not in cleaned
    assert "10492-A" not in cleaned
    assert "99812" not in cleaned
    # Ensure no empty parentheses remain
    assert "()" not in cleaned


def test_sanitize_edge_cases():
    assert sanitize_clinical_identifiers_and_timestamps(None) is None
    assert sanitize_clinical_identifiers_and_timestamps("") == ""
    assert sanitize_clinical_identifiers_and_timestamps("   ") == ""
    
    # Text already clean
    clean_text = "Patient underwent lumbar spine MRI on 2023-04-12 showing mild L4-L5 disc desiccation."
    assert sanitize_clinical_identifiers_and_timestamps(clean_text) == clean_text
