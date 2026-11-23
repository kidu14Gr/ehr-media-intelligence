"""Patient clinical context extractor.

Extracts patient demographics, clinical notes, diagnostic reports, and encounters
from SQLite storage or FHIR Bundles, decoding base64 attachments and formatting
the comprehensive clinical timeline for LLM summarization and content hashing.
"""
import base64
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from app.core.config import get_settings


@dataclass
class ExtractedDocument:
    document_id: str
    resource_type: str
    title: str
    date: Optional[str]
    category: Optional[str]
    text_content: str
    diagnoses: List[str] = field(default_factory=list)


@dataclass
class PatientClinicalContext:
    patient_id: str
    mrn: str
    facility_id: Optional[str]
    first_name: str
    last_name: str
    date_of_birth: str
    gender: str
    documents: List[ExtractedDocument]
    raw_source_text: str
    content_hash: str


class PatientContextExtractor:
    """Extracts and normalizes patient records from SQLite database."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or get_settings().database_path

    def extract_context(self, patient_id: str) -> Optional[PatientClinicalContext]:
        """Extracts complete clinical context for a patient_id."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # 1. Fetch Patient demographics
            cursor.execute(
                """
                SELECT patient_id, mrn, facility_id, first_name, last_name, date_of_birth, gender
                FROM patients
                WHERE patient_id = ?;
                """,
                (patient_id,),
            )
            pat_row = cursor.fetchone()
            if not pat_row:
                return None

            p_id, mrn, fac_id, first_name, last_name, dob, gender = pat_row

            # 2. Fetch associated FHIR resources
            cursor.execute(
                """
                SELECT resource_id, resource_type, effective_date, fhir_json
                FROM fhir_resources
                WHERE patient_id = ?
                ORDER BY effective_date ASC, resource_id ASC;
                """,
                (patient_id,),
            )
            res_rows = cursor.fetchall()

        extracted_docs: List[ExtractedDocument] = []
        raw_text_parts: List[str] = []

        for res_id, res_type, eff_date, fhir_json_str in res_rows:
            try:
                res_dict = json.loads(fhir_json_str)
            except Exception:
                continue

            if res_type == "DocumentReference":
                title = res_dict.get("type", {}).get("text", "Clinical Note")
                text_content = ""
                # Decode attachment narrative
                content_list = res_dict.get("content", [])
                for c in content_list:
                    att = c.get("attachment", {})
                    data_b64 = att.get("data")
                    if data_b64:
                        try:
                            # Pad base64 if needed
                            padded = data_b64 + "=" * (-len(data_b64) % 4)
                            text_content = base64.b64decode(padded).decode("utf-8")
                        except Exception:
                            text_content = data_b64
                    if att.get("title"):
                        title = att.get("title")

                extracted_docs.append(
                    ExtractedDocument(
                        document_id=res_id,
                        resource_type=res_type,
                        title=title,
                        date=eff_date,
                        category="Clinical Note",
                        text_content=text_content,
                    )
                )
                if text_content:
                    raw_text_parts.append(text_content)

            elif res_type == "DiagnosticReport":
                title = res_dict.get("code", {}).get("text", "Diagnostic Report")
                category = "Diagnostic"
                if res_dict.get("category"):
                    category = res_dict["category"][0].get("text", "Diagnostic")

                # Decode presentedForm or conclusion
                text_content = res_dict.get("conclusion") or ""
                presented = res_dict.get("presentedForm", [])
                for p in presented:
                    data_b64 = p.get("data")
                    if data_b64:
                        try:
                            padded = data_b64 + "=" * (-len(data_b64) % 4)
                            decoded = base64.b64decode(padded).decode("utf-8")
                            text_content = f"{text_content}\n{decoded}".strip()
                        except Exception:
                            pass
                    if p.get("title"):
                        title = p.get("title")

                diagnoses: List[str] = []
                if res_dict.get("conclusion"):
                    diagnoses.append(res_dict["conclusion"])

                extracted_docs.append(
                    ExtractedDocument(
                        document_id=res_id,
                        resource_type=res_type,
                        title=title,
                        date=eff_date,
                        category=category,
                        text_content=text_content,
                        diagnoses=diagnoses,
                    )
                )
                if text_content:
                    raw_text_parts.append(text_content)

        raw_source_text = "\n\n".join(raw_text_parts)
        content_hash = self.compute_content_hash(
            patient_id=p_id,
            dob=dob,
            gender=gender,
            documents=extracted_docs,
        )

        return PatientClinicalContext(
            patient_id=p_id,
            mrn=mrn,
            facility_id=fac_id,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=dob,
            gender=gender,
            documents=extracted_docs,
            raw_source_text=raw_source_text,
            content_hash=content_hash,
        )

    @staticmethod
    def compute_content_hash(
        patient_id: str,
        dob: str,
        gender: str,
        documents: List[ExtractedDocument],
    ) -> str:
        """Computes deterministic SHA-256 over normalized patient clinical facts."""
        components: List[str] = [
            f"PATIENT:{patient_id}",
            f"DOB:{dob}",
            f"GENDER:{gender.lower()}",
        ]
        # Sort documents by ID for stable hashing
        sorted_docs = sorted(documents, key=lambda d: d.document_id)
        for doc in sorted_docs:
            components.append(
                f"DOC:{doc.document_id}|{doc.date or ''}|{doc.title}|{doc.text_content.strip()}"
            )

        serialized = "\n".join(components).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()
