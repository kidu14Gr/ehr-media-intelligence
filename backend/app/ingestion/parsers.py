"""Parsers for JSON, CSV, and plain-text clinical documents.

Provides robust multi-format ingestion with:
- Format sniffing based on file extensions and content.
- Flexible header/key aliasing for heterogeneous EHR source fields.
- Verbatim clinical narrative preservation in text_content.
- Detection of unassigned clinical notes (missing patient identity).
"""
from abc import ABC, abstractmethod
import csv
from dataclasses import dataclass, field
import io
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass
class RawClinicalRecord:
    """Intermediate raw extraction from a source file before normalization."""
    patient_data: Dict[str, Any]
    document_data: Dict[str, Any]
    source_file: str
    source_record_id: Optional[str] = None
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    is_unassigned: bool = False
    unassigned_reason: Optional[str] = None


class BaseParser(ABC):
    """Abstract base class for all EHR format parsers."""

    @abstractmethod
    def parse(
        self,
        content: Union[str, bytes],
        source_file: str,
    ) -> List[RawClinicalRecord]:
        """Parse raw content into a list of RawClinicalRecord objects."""
        pass


# Common key aliases across EHR systems
PATIENT_KEY_ALIASES = {
    "mrn": ["mrn", "patient_mrn", "patient_id", "medical_record_number", "id"],
    "facility_id": ["facility_id", "facility", "hospital_id", "hospital", "clinic", "clinic_id"],
    "first_name": ["first_name", "given_name", "firstname", "fname"],
    "last_name": ["last_name", "family_name", "lastname", "lname"],
    "date_of_birth": ["date_of_birth", "dob", "birth_date", "birthdate"],
    "gender": ["gender", "sex", "administrative_gender"],
    "national_id": ["national_id", "ssn", "civil_id"],
    "phone": ["phone", "telephone", "phone_number"],
    "email": ["email", "email_address"],
    "address": ["address", "street_address"],
}

DOCUMENT_KEY_ALIASES = {
    "document_id": ["document_id", "doc_id", "note_id", "report_id"],
    "encounter_id": ["encounter_id", "encounter", "visit_id", "admission_id"],
    "document_type": ["document_type", "doc_type", "type", "category", "note_type"],
    "effective_date": ["effective_date", "document_date", "date", "encounter_date", "note_date", "timestamp"],
    "title": ["title", "document_title", "subject", "header"],
    "text_content": [
        "text_content",
        "clinical_narrative",
        "clinical_note",
        "note",
        "text",
        "report_text",
        "narrative",
        "body",
        "content",
    ],
    "author": ["author", "clinician", "provider", "physician", "doctor"],
    "facility": ["facility", "hospital", "clinic", "location"],
    "status": ["status", "document_status"],
    "diagnoses": ["diagnoses", "diagnosis", "impressions", "findings"],
    "structured_data": ["structured_data", "data", "vitals", "labs", "parameters"],
    "media_mime_type": ["media_mime_type", "mime_type", "content_type"],
    "media_file_path": ["media_file_path", "file_path", "path"],
}


def _extract_aliased_keys(
    source_dict: Dict[str, Any],
    alias_map: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Extracts standardized keys from a dictionary using alias lists."""
    normalized_source = {str(k).strip().lower(): v for k, v in source_dict.items()}
    result: Dict[str, Any] = {}

    for target_key, aliases in alias_map.items():
        for alias in aliases:
            if alias in normalized_source and normalized_source[alias] not in (None, "", "null", "N/A", "None"):
                result[target_key] = normalized_source[alias]
                break

    return result


class JSONParser(BaseParser):
    """Parser for JSON payloads (single object or list of objects)."""

    def parse(
        self,
        content: Union[str, bytes],
        source_file: str,
    ) -> List[RawClinicalRecord]:
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        data = json.loads(text)

        raw_items: List[Dict[str, Any]] = data if isinstance(data, list) else [data]
        records: List[RawClinicalRecord] = []

        for index, item in enumerate(raw_items):
            record_id = str(item.get("id") or item.get("record_id") or index + 1)
            
            # Check for nested patient/documents structure
            if "patient" in item and isinstance(item["patient"], dict):
                patient_data = _extract_aliased_keys(item["patient"], PATIENT_KEY_ALIASES)
                docs = item.get("documents") or item.get("notes") or []
                if isinstance(docs, list) and docs:
                    for doc_idx, doc_item in enumerate(docs):
                        doc_data = _extract_aliased_keys(doc_item, DOCUMENT_KEY_ALIASES)
                        records.append(
                            RawClinicalRecord(
                                patient_data=patient_data,
                                document_data=doc_data,
                                source_file=source_file,
                                source_record_id=f"{record_id}.{doc_idx + 1}",
                                raw_payload=item,
                            )
                        )
                    continue
                else:
                    doc_data = _extract_aliased_keys(item, DOCUMENT_KEY_ALIASES)
                    records.append(
                        RawClinicalRecord(
                            patient_data=patient_data,
                            document_data=doc_data,
                            source_file=source_file,
                            source_record_id=record_id,
                            raw_payload=item,
                        )
                    )
                    continue

            # Flat structure
            patient_data = _extract_aliased_keys(item, PATIENT_KEY_ALIASES)
            document_data = _extract_aliased_keys(item, DOCUMENT_KEY_ALIASES)

            records.append(
                RawClinicalRecord(
                    patient_data=patient_data,
                    document_data=document_data,
                    source_file=source_file,
                    source_record_id=record_id,
                    raw_payload=item,
                )
            )

        return records


class CSVParser(BaseParser):
    """Parser for delimited files (CSV, TSV) with delimiter sniffing and header mapping."""

    def parse(
        self,
        content: Union[str, bytes],
        source_file: str,
    ) -> List[RawClinicalRecord]:
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        f = io.StringIO(text.strip())

        # Sniff delimiter safely
        sample = text[:2048]
        delimiter = ","
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
            delimiter = dialect.delimiter
        except Exception:
            if "\t" in sample and "," not in sample:
                delimiter = "\t"
            elif ";" in sample and "," not in sample:
                delimiter = ";"

        reader = csv.DictReader(f, delimiter=delimiter)
        records: List[RawClinicalRecord] = []

        for row_idx, row in enumerate(reader, start=1):
            cleaned_row = {
                k.strip(): (v.strip() if isinstance(v, str) else v)
                for k, v in row.items()
                if k is not None
            }
            patient_data = _extract_aliased_keys(cleaned_row, PATIENT_KEY_ALIASES)
            document_data = _extract_aliased_keys(cleaned_row, DOCUMENT_KEY_ALIASES)

            records.append(
                RawClinicalRecord(
                    patient_data=patient_data,
                    document_data=document_data,
                    source_file=source_file,
                    source_record_id=str(row_idx),
                    raw_payload=cleaned_row,
                )
            )

        return records


class PlainTextParser(BaseParser):
    """Parser for unstructured plain-text clinical documents.
    
    Extracts header metadata when present while preserving the original clinical
    narrative in its entirety. Quarantines documents when patient identity cannot
    be established.
    """

    PATIENT_PATTERNS = {
        "name": re.compile(r"^(?:Patient(?:\s+Name)?|Name)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "mrn": re.compile(r"^(?:MRN|Medical\s*Record\s*Number)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "date_of_birth": re.compile(r"^(?:DOB|Date\s*of\s*Birth|Birth\s*Date)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "gender": re.compile(r"^(?:Gender|Sex)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "facility_id": re.compile(r"^(?:Facility|Hospital|Clinic)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    }

    DOCUMENT_PATTERNS = {
        "encounter_id": re.compile(r"^(?:Encounter(?:\s*ID)?|Visit\s*ID)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "document_type": re.compile(r"^(?:Document\s*Type|Note\s*Type|Type)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "effective_date": re.compile(r"^(?:Date|Encounter\s*Date|Note\s*Date)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "author": re.compile(r"^(?:Author|Provider|Physician|Doctor)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        "title": re.compile(r"^(?:Title|Subject)\s*:\s*(?P<val>[^\n\r]+)", re.IGNORECASE | re.MULTILINE),
    }

    def parse(
        self,
        content: Union[str, bytes],
        source_file: str,
    ) -> List[RawClinicalRecord]:
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        raw_text = text.strip()

        patient_data: Dict[str, Any] = {}
        document_data: Dict[str, Any] = {
            "text_content": raw_text,  # Preserve entire narrative verbatim
        }

        # Extract patient metadata
        for field_name, pattern in self.PATIENT_PATTERNS.items():
            match = pattern.search(raw_text)
            if match:
                val = match.group("val").strip()
                if field_name == "name":
                    # Split full name into first and last name
                    parts = val.split(",", 1)
                    if len(parts) == 2:
                        patient_data["last_name"] = parts[0].strip()
                        patient_data["first_name"] = parts[1].strip()
                    else:
                        name_parts = val.split()
                        if len(name_parts) >= 2:
                            patient_data["first_name"] = name_parts[0].strip()
                            patient_data["last_name"] = " ".join(name_parts[1:]).strip()
                        elif name_parts:
                            patient_data["first_name"] = name_parts[0].strip()
                            patient_data["last_name"] = "UNKNOWN"
                else:
                    patient_data[field_name] = val

        # Extract document metadata
        for field_name, pattern in self.DOCUMENT_PATTERNS.items():
            match = pattern.search(raw_text)
            if match:
                document_data[field_name] = match.group("val").strip()

        # Check if patient identity is missing
        is_unassigned = False
        unassigned_reason = None
        if not patient_data.get("mrn") and not (patient_data.get("first_name") and patient_data.get("date_of_birth")):
            is_unassigned = True
            unassigned_reason = (
                "Unassigned clinical note: No patient identity (MRN or Name + DOB) "
                "could be extracted from plain text header."
            )

        return [
            RawClinicalRecord(
                patient_data=patient_data,
                document_data=document_data,
                source_file=source_file,
                source_record_id="1",
                raw_payload={"raw_text": raw_text},
                is_unassigned=is_unassigned,
                unassigned_reason=unassigned_reason,
            )
        ]


def get_parser_for_file(file_name_or_path: str) -> BaseParser:
    """Factory selecting appropriate parser by file extension."""
    lower_path = file_name_or_path.lower()
    if lower_path.endswith(".json"):
        return JSONParser()
    elif lower_path.endswith((".csv", ".tsv")):
        return CSVParser()
    elif lower_path.endswith((".txt", ".text", ".note", ".md")):
        return PlainTextParser()
    else:
        # Default to plain text
        return PlainTextParser()
