#!/usr/bin/env python3
"""CLI runner script for FHIR R4B Normalization & Persistence Pipeline (Task 2).

Reads standardized intermediate records from data/processed/cleaned_records.json,
transforms them into standard HL7 FHIR R4B resource Bundles, validates referential
integrity and schema structure, and persists to both JSON files and SQLite with
idempotent upserts.
"""
import argparse
import json
from pathlib import Path
import sys
import time

# Ensure backend root is on Python path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.fhir.converter import FHIRConverter
from app.fhir.storage import DualFHIRStorage
from app.fhir.validator import FHIRValidator
from app.schemas.intermediate import IntermediateRecord


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run FHIR R4B Normalization and Persistence Pipeline."
    )
    default_root = backend_dir.parent
    parser.add_argument(
        "--input-file",
        type=Path,
        default=default_root / "data" / "processed" / "cleaned_records.json",
        help="Path to cleaned_records.json produced by Task 1 ingestion pipeline",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_root / "data" / "processed" / "fhir",
        help="Directory to persist individual and combined FHIR JSON bundles",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=default_root / "storage" / "clinical_platform.db",
        help="Path to SQLite database for clinical platform storage",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_file: Path = args.input_file
    output_dir: Path = args.output_dir
    db_path: Path = args.db_path

    if not input_file.exists():
        print(f"Error: Input file not found: {input_file}", file=sys.stderr)
        print("Please run the Task 1 ingestion pipeline first: python scripts/run_ingestion.py", file=sys.stderr)
        return 1

    print("================================================================================")
    print("      EHR MEDIA INTELLIGENCE PLATFORM — FHIR R4B NORMALIZATION (TASK 2)         ")
    print("================================================================================")
    print(f"Input Records     : {input_file}")
    print(f"FHIR JSON Dir     : {output_dir}")
    print(f"SQLite Storage DB : {db_path}")
    print("--------------------------------------------------------------------------------")

    start_time = time.perf_counter()

    # 1. Load intermediate records
    try:
        raw_data = json.loads(input_file.read_text(encoding="utf-8"))
        records = [IntermediateRecord.model_validate(item) for item in raw_data]
    except Exception as e:
        print(f"Error reading or parsing input intermediate records: {e}", file=sys.stderr)
        return 1

    print(f"Loaded {len(records)} intermediate record(s) for conversion.\n")

    # 2. Convert to FHIR R4B Bundles
    print("1. Converting intermediate records to HL7 FHIR R4B Bundles...")
    converter = FHIRConverter()
    bundles = converter.convert_records(records)
    print(f"   -> Generated {len(bundles)} collection Bundle(s).")

    # 3. Validate Bundles
    print("2. Validating FHIR Bundles structure & referential integrity...")
    validator = FHIRValidator()
    report = validator.validate_bundles(bundles)
    print(f"   -> Validation Status : {'PASSED (Valid)' if report.is_valid else 'FAILED (Invalid)'}")
    print(f"   -> Issues Found      : {len(report.issues)} issue(s)")
    for issue in report.issues:
        prefix = "ERROR" if issue.severity.value == "error" else "WARNING"
        print(f"      [{prefix}] Bundle {issue.bundle_id or 'N/A'}: {issue.message} ({issue.details or ''})")

    # 4. Persist to JSON and SQLite
    print("3. Persisting FHIR resources to dual storage (JSON + SQLite)...")
    storage = DualFHIRStorage(json_dir=output_dir, db_path=db_path)
    storage.store_bundles(bundles)
    storage.store_validation_report(report)
    print(f"   -> Individual JSON bundles saved to : {output_dir}/<bundle_id>.json")
    print(f"   -> Consolidated bundle saved to     : {output_dir}/all_fhir_bundles.json")
    print(f"   -> Validation report saved to       : {output_dir}/fhir_validation_report.json")
    print(f"   -> SQLite tables updated at         : {db_path}")

    elapsed = time.perf_counter() - start_time

    # 5. Summary Presentation
    print("\n================================================================================")
    print("                         TASK 2 EXECUTION SUMMARY                               ")
    print("================================================================================")
    print(f"Records Ingested       : {len(records)}")
    print(f"Bundles Generated      : {len(bundles)}")
    print(f"Total FHIR Resources   : {report.total_resources}")
    print("Resources by Type:")
    for res_type, count in sorted(report.resource_counts.items()):
        print(f"  • {res_type:<20} : {count}")
    print(f"Referential Validation : {'VALID' if report.is_valid else 'INVALID'}")
    print(f"Total Validation Issues: {len(report.issues)}")
    print(f"Execution Duration     : {elapsed:.3f} seconds")
    print("================================================================================\n")

    return 0 if report.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
