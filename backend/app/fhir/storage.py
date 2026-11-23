"""Storage layer for FHIR R4B bundles and resources (JSON + SQLite).

Implements:
- Small abstract interface: BaseFHIRStorage
- Concrete implementation: DualFHIRStorage (persists to JSON and SQLite)
- Idempotent SQLite upserts using ON CONFLICT clauses
- Indexed tables: patients, fhir_resources, fhir_bundles
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.patient import Patient

from app.core.exceptions import FHIRStorageError
from app.schemas.fhir_report import FHIRValidationReport


class BaseFHIRStorage(ABC):
    """Abstract interface for FHIR storage operations."""

    @abstractmethod
    def store_bundles(self, bundles: List[Bundle]) -> None:
        """Stores a collection of FHIR Bundles."""
        pass

    @abstractmethod
    def store_validation_report(self, report: FHIRValidationReport) -> None:
        """Stores a FHIR validation report."""
        pass

    @abstractmethod
    def get_bundle(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single bundle by ID."""
        pass


class DualFHIRStorage(BaseFHIRStorage):
    """Dual persistence engine storing to both SQLite database and JSON files.
    
    Guarantees idempotence through SQL ON CONFLICT upsert clauses.
    """

    def __init__(
        self,
        json_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        backend_dir = Path(__file__).resolve().parents[2]
        self.json_dir = json_dir or (backend_dir.parent / "data" / "processed" / "fhir")
        self.db_path = db_path or (backend_dir.parent / "storage" / "clinical_platform.db")
        self._init_storage()

    def _init_storage(self) -> None:
        """Initializes directory structure and SQLite database schema."""
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 1. Patients relational table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS patients (
                    patient_id TEXT PRIMARY KEY,
                    mrn TEXT NOT NULL,
                    facility_id TEXT,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    date_of_birth TEXT NOT NULL,
                    gender TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            # 2. Individual FHIR resources table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS fhir_resources (
                    resource_id TEXT PRIMARY KEY,
                    resource_type TEXT NOT NULL,
                    patient_id TEXT NOT NULL,
                    encounter_id TEXT,
                    effective_date TEXT,
                    fhir_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
                );
                """
            )
            # 3. Patient Bundles table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS fhir_bundles (
                    bundle_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    resource_count INTEGER NOT NULL,
                    bundle_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
                );
                """
            )
            # Indices for rapid querying
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_patient ON fhir_resources(patient_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_resources_type ON fhir_resources(resource_type);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_bundles_patient ON fhir_bundles(patient_id);")
            conn.commit()

    def store_bundles(self, bundles: List[Bundle]) -> None:
        """Stores bundles to both individual/combined JSON files and SQLite via idempotent upserts."""
        if not bundles:
            return

        combined_bundles_json: List[Dict[str, Any]] = []

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                for bundle in bundles:
                    bundle_raw_json = bundle.model_dump_json(indent=2)
                    bundle_dict = json.loads(bundle_raw_json)
                    combined_bundles_json.append(bundle_dict)

                    # Save individual JSON bundle file (avoid doubled bundle_ prefix)
                    bundle_filename = (
                        f"{bundle.id}.json"
                        if bundle.id and bundle.id.startswith("bundle-")
                        else f"bundle-{bundle.id}.json"
                    )
                    bundle_file = self.json_dir / bundle_filename
                    bundle_file.write_text(bundle_raw_json, encoding="utf-8")

                    # Identify Patient entry
                    patient_res: Optional[Patient] = None
                    for entry in bundle.entry or []:
                        if isinstance(entry.resource, Patient):
                            patient_res = entry.resource
                            break

                    if not patient_res:
                        continue

                    patient_id = patient_res.id or f"pat-{bundle.id}"
                    mrn = "UNKNOWN"
                    facility_id = None
                    for ident in patient_res.identifier or []:
                        if ident.system and "/mrn" in ident.system:
                            mrn = ident.value or mrn
                            # Extract facility from system if formatted
                            parts = ident.system.split("/")
                            if len(parts) >= 2 and parts[-1] == "mrn" and parts[-2] != "default":
                                facility_id = parts[-2]
                            break

                    first_name = ""
                    last_name = ""
                    if patient_res.name:
                        first_name = (
                            patient_res.name[0].given[0]
                            if patient_res.name[0].given
                            else ""
                        )
                        last_name = patient_res.name[0].family or ""

                    now_iso = datetime.now(timezone.utc).isoformat()

                    # Idempotent Patient upsert
                    cursor.execute(
                        """
                        INSERT INTO patients (patient_id, mrn, facility_id, first_name, last_name, date_of_birth, gender, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(patient_id) DO UPDATE SET
                            mrn=excluded.mrn,
                            facility_id=excluded.facility_id,
                            first_name=excluded.first_name,
                            last_name=excluded.last_name,
                            date_of_birth=excluded.date_of_birth,
                            gender=excluded.gender,
                            created_at=excluded.created_at;
                        """,
                        (
                            patient_id,
                            mrn,
                            facility_id,
                            first_name,
                            last_name,
                            str(patient_res.birthDate or ""),
                            patient_res.gender or "unknown",
                            now_iso,
                        ),
                    )

                    # Idempotent Resource upserts
                    for entry in bundle.entry or []:
                        res = entry.resource
                        res_id = getattr(res, "id", f"res-{bundle.id}")
                        res_type = res.__class__.__name__

                        enc_id = None
                        eff_date = None

                        if isinstance(res, Encounter):
                            enc_id = res.id
                        elif isinstance(res, DocumentReference):
                            if res.context and res.context.encounter:
                                enc_id = res.context.encounter[0].reference
                            eff_date = str(res.date) if res.date else None
                        elif isinstance(res, DiagnosticReport):
                            if res.encounter:
                                enc_id = res.encounter.reference
                            eff_date = str(res.effectiveDateTime) if res.effectiveDateTime else None

                        res_json = res.model_dump_json()

                        cursor.execute(
                            """
                            INSERT INTO fhir_resources (resource_id, resource_type, patient_id, encounter_id, effective_date, fhir_json, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(resource_id) DO UPDATE SET
                                resource_type=excluded.resource_type,
                                patient_id=excluded.patient_id,
                                encounter_id=excluded.encounter_id,
                                effective_date=excluded.effective_date,
                                fhir_json=excluded.fhir_json,
                                created_at=excluded.created_at;
                            """,
                            (
                                res_id,
                                res_type,
                                patient_id,
                                enc_id,
                                eff_date,
                                res_json,
                                now_iso,
                            ),
                        )

                    # Idempotent Bundle upsert
                    cursor.execute(
                        """
                        INSERT INTO fhir_bundles (bundle_id, patient_id, resource_count, bundle_json, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(bundle_id) DO UPDATE SET
                            patient_id=excluded.patient_id,
                            resource_count=excluded.resource_count,
                            bundle_json=excluded.bundle_json,
                            created_at=excluded.created_at;
                        """,
                        (
                            bundle.id or f"bundle-{patient_id}",
                            patient_id,
                            len(bundle.entry or []),
                            bundle_raw_json,
                            now_iso,
                        ),
                    )

                conn.commit()

            # Save combined all_fhir_bundles.json
            all_bundles_path = self.json_dir / "all_fhir_bundles.json"
            all_bundles_path.write_text(json.dumps(combined_bundles_json, indent=2), encoding="utf-8")

        except Exception as e:
            raise FHIRStorageError(f"Failed to persist FHIR resources to storage: {e}") from e

    def store_validation_report(self, report: FHIRValidationReport) -> None:
        """Persists the validation report as a formatted JSON document."""
        report_file = self.json_dir / "fhir_validation_report.json"
        report_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    def get_bundle(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a bundle by bundle_id from SQLite or JSON."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT bundle_json FROM fhir_bundles WHERE bundle_id = ?", (bundle_id,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])

        bundle_filename = (
            f"{bundle_id}.json"
            if bundle_id.startswith("bundle-")
            else f"bundle-{bundle_id}.json"
        )
        bundle_file = self.json_dir / bundle_filename
        if bundle_file.exists():
            return json.loads(bundle_file.read_text(encoding="utf-8"))

        return None
