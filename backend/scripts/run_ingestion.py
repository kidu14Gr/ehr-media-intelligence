#!/usr/bin/env python3
"""CLI runner script for EHR Media Intelligence Ingestion Pipeline (Task 1).

Reads heterogeneous source files from data/samples/, executes the end-to-end
ingestion pipeline, exports cleaned intermediate records and audit trails to
data/processed/, and prints a detailed execution summary.
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

from app.ingestion.pipeline import IngestionPipeline
from app.schemas.enums import ProcessingStatus


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run EHR Data Ingestion and Normalization Pipeline."
    )
    default_root = backend_dir.parent
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=default_root / "data" / "samples",
        help="Directory containing source EHR files (JSON, CSV, TXT)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_root / "data" / "processed",
        help="Directory where cleaned records and audit logs are saved",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    samples_dir: Path = args.samples_dir
    output_dir: Path = args.output_dir

    if not samples_dir.exists():
        print(f"Error: Samples directory not found: {samples_dir}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect source files
    supported_extensions = {".json", ".csv", ".tsv", ".txt", ".note"}
    sample_files = sorted(
        [p for p in samples_dir.iterdir() if p.is_file() and p.suffix.lower() in supported_extensions]
    )

    if not sample_files:
        print(f"Warning: No valid sample files found in {samples_dir}", file=sys.stderr)
        return 0

    print("================================================================================")
    print("       EHR MEDIA INTELLIGENCE PLATFORM — DATA INGESTION PIPELINE (TASK 1)       ")
    print("================================================================================")
    print(f"Input Directory  : {samples_dir}")
    print(f"Output Directory : {output_dir}")
    print(f"Found {len(sample_files)} source file(s) for ingestion:")
    for f in sample_files:
        print(f"  • {f.name} ({f.stat().st_size} bytes)")
    print("--------------------------------------------------------------------------------")
    print("Executing ingestion and normalization pipeline...")

    start_time = time.perf_counter()
    pipeline = IngestionPipeline()
    batch_result = pipeline.ingest_files(sample_files)
    elapsed = time.perf_counter() - start_time

    # Output file paths
    cleaned_records_path = output_dir / "cleaned_records.json"
    audit_log_path = output_dir / "ingestion_audit_log.json"
    quarantined_path = output_dir / "quarantined_records.json"

    # Serialize cleaned records
    cleaned_payload = [rec.model_dump(mode="json") for rec in batch_result.successful_records]
    cleaned_records_path.write_text(json.dumps(cleaned_payload, indent=2), encoding="utf-8")

    # Serialize audit log
    audit_payload = [log.model_dump(mode="json") for log in batch_result.audit_logs]
    audit_log_path.write_text(json.dumps(audit_payload, indent=2), encoding="utf-8")

    # Serialize quarantined records
    quarantined_payload = [q.model_dump(mode="json") for q in batch_result.quarantined_records]
    quarantined_path.write_text(json.dumps(quarantined_payload, indent=2), encoding="utf-8")

    # Metrics computation
    total_docs = sum(len(rec.documents) for rec in batch_result.successful_records)
    total_quarantined = len(batch_result.quarantined_records)
    total_duplicates = batch_result.duplicate_count
    total_audits = len(batch_result.audit_logs)

    warning_count = sum(1 for a in batch_result.audit_logs if a.status == ProcessingStatus.NORMALIZED_WITH_WARNINGS)
    conflict_count = sum(1 for a in batch_result.audit_logs if a.status == ProcessingStatus.CONFLICT_DETECTED)
    unassigned_count = sum(1 for a in batch_result.audit_logs if a.status == ProcessingStatus.QUARANTINED)

    print("\n================================================================================")
    print("                            INGESTION BATCH SUMMARY                             ")
    print("================================================================================")
    print(f"Total Raw Records Processed : {batch_result.total_processed}")
    print(f"Successful Patient Bundles  : {len(batch_result.successful_records)}")
    print(f"Total Documents Accumulated : {total_docs}")
    print(f"Duplicate Documents Ignored : {total_duplicates}")
    print(f"Quarantined Records         : {total_quarantined}")
    print(f"  ├─ Identity Conflicts     : {conflict_count}")
    print(f"  └─ Unassigned Plain Text  : {unassigned_count}")
    print(f"Total Audit Trail Entries   : {total_audits} (Warnings: {warning_count})")
    print(f"Pipeline Execution Time     : {elapsed:.3f} seconds")
    print("--------------------------------------------------------------------------------")
    print("Generated Artifacts for Task 2 (FHIR Conversion):")
    print(f"  ✔ Cleaned Records JSON     : {cleaned_records_path}")
    print(f"  ✔ Forensic Audit Log JSON  : {audit_log_path}")
    print(f"  ✔ Quarantined Records JSON : {quarantined_path}")
    print("================================================================================\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
