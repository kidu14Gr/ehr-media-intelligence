#!/usr/bin/env python3
"""CLI runner script for LLM Clinical Summarization Pipeline (Task 3).

Processes all cohort patients in SQLite storage, generates or retrieves structured
clinical summaries, enforces strict word limits (< 200 words), separates documented
facts from interpretations, verifies diagnoses against source texts, and records
transparent confidence metrics.
"""
import argparse
from pathlib import Path
import sqlite3
import sys
import time

# Ensure backend root is on Python path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ai.summarizer import ClinicalSummarizer
from app.core.config import get_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LLM-Powered Clinical Summarization Pipeline."
    )
    parser.add_argument(
        "--patient-id",
        type=str,
        default=None,
        help="Optional single patient ID to summarize (e.g. pat-REC-3703B61FE28A)",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force regeneration and bypass cache",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()

    print("================================================================================")
    print("      EHR MEDIA INTELLIGENCE PLATFORM — CLINICAL SUMMARIZATION (TASK 3)         ")
    print("================================================================================")
    print(f"LLM Provider      : {settings.llm_provider}")
    print(f"Active Model      : {settings.get_active_model_name()}")
    print(f"Prompt Version    : {settings.prompt_version}")
    print(f"Word Target/Limit : ~{settings.summary_word_target} target / {settings.summary_word_limit} max")
    print(f"Database Path     : {settings.database_path}")
    print(f"Cache Directory   : {settings.summary_cache_dir}")
    print("--------------------------------------------------------------------------------")

    if not settings.database_path.exists():
        print(f"Error: Database not found at {settings.database_path}", file=sys.stderr)
        return 1

    summarizer = ClinicalSummarizer()

    # Query target patients
    with sqlite3.connect(settings.database_path) as conn:
        cursor = conn.cursor()
        if args.patient_id:
            cursor.execute(
                "SELECT patient_id FROM patients WHERE patient_id = ?;",
                (args.patient_id,),
            )
        else:
            cursor.execute(
                "SELECT patient_id FROM patients ORDER BY patient_id ASC;"
            )
        patients = cursor.fetchall()

    if not patients:
        print("No patients found for summarization.", file=sys.stderr)
        return 1

    print(f"Loaded {len(patients)} cohort patient(s) for summarization.\n")

    start_time = time.perf_counter()
    summaries = []
    cache_hits = 0

    for idx, (p_id,) in enumerate(patients, start=1):
        print(f"[{idx}/{len(patients)}] Processing Patient: {p_id}...")
        t0 = time.perf_counter()
        try:
            summary = summarizer.summarize_patient(p_id, force_refresh=args.force_refresh)
            elapsed_p = time.perf_counter() - t0
            summaries.append(summary)

            if summary.is_cached:
                cache_hits += 1
                status_tag = "[CACHE HIT]"
            else:
                status_tag = "[API GENERATED]"

            print(f"      Status       : {status_tag} in {elapsed_p:.3f}s")
            print(f"      Chief Concern: {summary.chief_concern or 'Not documented'}")
            print(f"      Diagnoses    : {summary.documented_diagnoses or 'None documented'}")
            if summary.unverified_or_flagged_diagnoses:
                print(f"      [FLAGGED]    : {summary.unverified_or_flagged_diagnoses} (not in source text)")
            print(f"      Word Count   : {summary.word_count} words (limit: {settings.summary_word_limit})")
            print(f"      Confidence   : {summary.confidence_score:.2f} ({len(summary.confidence_reasons)} factor(s))")
            print()

        except Exception as e:
            print(f"      Error generating summary for {p_id}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()

    total_elapsed = time.perf_counter() - start_time
    total_calls = len(patients) - cache_hits

    print("================================================================================")
    print("                         TASK 3 EXECUTION SUMMARY                               ")
    print("================================================================================")
    print(f"Cohort Size         : {len(patients)} patients")
    print(f"Summaries Produced  : {len(summaries)}")
    print(f"Cache Hits          : {cache_hits}")
    print(f"LLM API Calls Made  : {total_calls}")
    print(f"Total Duration      : {total_elapsed:.3f} seconds")
    print("================================================================================\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
