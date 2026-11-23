# EHR Media Intelligence Platform

An AI-powered Electronic Health Record (EHR) intelligence platform designed to transform unstructured clinical documents into standardized, searchable, and meaningful healthcare information.

## Project Overview

The platform processes heterogeneous EHR documents, cleans and normalizes clinical data using HL7 FHIR R4, generates AI-powered clinical summaries, and enables semantic search across patient records.

## Core Features

- **Data Ingestion & Cleaning:** Process JSON, CSV, and plain-text clinical records.
- **FHIR R4 Normalization:** Convert cleaned records into validated FHIR resources.
- **AI Clinical Summarization:** Generate concise, structured summaries using Large Language Models.
- **Semantic Search:** Retrieve relevant clinical records using vector embeddings.
- **Clinician Dashboard:** Search, filter, and review patient records through a web interface.

## Technology Stack

### Backend
- Python 3.11+
- FastAPI
- Pydantic v2
- FHIR Resources
- SQLite
- ChromaDB
- Sentence Transformers
- Pytest

### Frontend
- React
- Tailwind CSS
- JavaScript / TypeScript

## Project Structure

```text
backend/     Python API and application logic
frontend/    React clinician dashboard
data/        Sample, uploaded, and processed documents
storage/     Local database and vector persistence
docs/        Architecture and assessment documentation
```

## Development Status

- [x] **Task 1: Data Ingestion and Cleaning** — Heterogeneous parsing (JSON, CSV, plain-text), conservative normalization (MRN, DOB, gender), in-memory identity deduplication, and forensic audit logging.
- [x] **Task 2: FHIR R4B Normalization & Persistence** — Standards-compliant transformation to HL7 FHIR R4B, structural validation, and dual persistence (JSON + SQLite).
- [x] **Task 3: AI-Powered Clinical Summarization** — Groq-hosted open LLM inference, clinical safety guardrails (diagnoses verification, no hallucinated recommendations), dual caching (SQLite + JSON), and input-completeness confidence scoring.
- [ ] **Task 4: Semantic Search Engine**
- [ ] **Task 5: Clinician Web Interface**

## Task 2 Architecture & Design Decisions

### 1. HL7 FHIR R4B Resource Modeling
- **Library & Versioning:** Built strictly with `fhir.resources.R4B` (Pydantic v2-backed), ensuring strict adherence to the FHIR R4B specification and avoiding default R5 model incompatibilities.
- **Resource Selection:**
  - `Patient`: Formatted with deterministic ID `pat-{record_id}`, official MRN system (`http://hospital.smarthealth.org/{facility_id}/mrn`), and `urn:example:national-id` for national identifiers.
  - `DocumentReference`: Used for clinical progress notes, outpatient records, and discharge summaries, embedding full clinical narratives as UTF-8 base64-encoded `Attachment` payloads.
  - `DiagnosticReport`: Used for laboratory (`LAB`) and medical imaging (`RAD`) reports.
  - `Encounter`: Minimal encounters generated per distinct `encounter_id`.
  - `Bundle`: Packaged into patient-level `collection` Bundles. Per HL7 FHIR R4 specification, collection bundles omit the `total` property (which is reserved for `searchset` or `history` bundles).

### 2. Referential Integrity & URN UUID Identifiers
- **URN UUID Mapping:** Every resource entry inside a Bundle is assigned a deterministic, collision-free `fullUrl` formatted as `urn:uuid:<uuid5>` (e.g. `urn:uuid:4b5aee4b-...`).
- **Strict Reference Consistency:** All inter-resource references (`subject.reference`, `encounter.reference`, `context.encounter`) strictly resolve using the matching `urn:uuid:...` identifier. Mixing absolute URNs with relative references (e.g., `Patient/pat-1`) is prohibited, ensuring complete referential self-containment within each Bundle.

### 3. Placeholder Encounter Class
- **Clinical Rationale:** Ingested EHR records often carry an administrative encounter code without specifying the encounter setting (inpatient, outpatient, emergency).
- **HL7 ActCode Placeholder:** Encounters are synthesized with `status="unknown"` and a standard placeholder class from HL7 v3 ActCode:
  ```json
  "class": {
    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
    "code": "UNK",
    "display": "unknown"
  }
  ```
- If a document record lacks an `encounter_id`, no `Encounter` resource is created, and the document reference context omits encounter links.

### 4. Structural Validation & Scope Boundaries
- **Two-Layer Validation via `FHIRValidator`:**
  1. *Model Validation:* Validates resource schema constraints, field cardinality, data types, and required attributes.
  2. *Referential Integrity Check:* Verifies that every `subject` and `encounter` reference within a bundle resolves to an existing entry in that same bundle.
- **Scope Boundary:** This validator performs structural and referential verification. It does not validate external implementation profiles (e.g., US Core profiles) or perform live terminology validation against external code systems (e.g., SNOMED CT, LOINC servers). Bundles can optionally be validated against external validator suites like HAPI FHIR.

### 5. Exclusion of Quarantined Records
- **Safety Boundary:** Data integrity and patient safety require that records with uncertain identity are never converted into FHIR resources.
- Only records that pass Task 1 identity resolution (`successful_records`) are converted. Records with identity conflicts or unassigned notes lacking patient headers are isolated in `data/processed/quarantined_records.json` and excluded from FHIR bundles and database persistence.

### 6. Clinical Date Integrity
- Documents lacking an explicit clinical date retain `effective_date = null` from Task 1. The FHIR `date` or `effectiveDateTime` property is **completely omitted** rather than falsified using ingestion or system timestamps.

### 7. Dual Persistence Architecture
- Defined behind the `BaseFHIRStorage` abstract interface with a concrete `DualFHIRStorage` implementation:
  - **JSON Storage:** Writes individual patient bundles to `data/processed/fhir/<bundle_id>.json`, consolidated bundles to `all_fhir_bundles.json`, and validation reports to `fhir_validation_report.json`.
  - **SQLite Relational Storage (`storage/clinical_platform.db`):** Maintains indexed tables (`patients`, `fhir_resources`, `fhir_bundles`).
  - **Idempotence:** Uses SQL `ON CONFLICT(...) DO UPDATE SET` upsert clauses across all tables, guaranteeing that repeated pipeline executions are safe and do not produce duplicate records.

## Task 3 Architecture & Design Decisions

### 1. Configurable LLM Provider & Open Model Inference
- **Provider & Model:** Powered by Groq fast inference utilizing the open-weight model `openai/gpt-oss-120b`.
- **Runtime Configurability:** All LLM provider parameters (`LLM_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT_SECONDS`, `PROMPT_VERSION`) are loaded dynamically via `pydantic-settings` from `backend/.env`. Provider architecture abstracts clients behind `BaseLLMClient`, allowing drop-in switching to alternative backends or `MockLLMClient` without touching application logic.

### 2. Privacy & De-Identification Safeguards
- **Zero Direct PHI in Prompts:** Clinical summaries are generated without leaking Patient Identifiable Information. Prompts exclude patient names, medical record numbers (MRNs), and facility names.
- **De-identified Envelope:** Only the surrogate patient ID (`pat-{record_id}`), patient age, and gender are provided as demographic context alongside decoded clinical attachments and laboratory observations.
- **Privacy-Preserving Telemetry:** Application logs record only anonymous patient IDs and performance metrics; no patient names, MRNs, or raw narrative text appear in server or pipeline logs.

### 3. Clinical Safety & Strict Grounding Guardrails
- **Separation of Documented Facts vs. AI Interpretation:**
  - `key_findings` contains only clinical measurements, lab results, and observations explicitly present in the source records.
  - Clinical reasoning and synthesis are strictly isolated within `narrative_summary` under an explicit `"Interpretation:"` prefix.
- **Programmatic Diagnosis Verification:**
  - Candidate diagnoses generated by the LLM undergo deterministic post-generation validation against the raw source text.
  - Any diagnosis not corroborated by verbatim clinical text is stripped from `documented_diagnoses` and quarantined into `unverified_or_flagged_diagnoses`.
  - The system never invents diagnoses, symptoms, or findings.
- **Narrative Restatement Constraints:**
  - The model is prohibited from inserting boilerplate negative statements (such as "denies symptoms" or "no acute distress") unless explicitly stated in the source documentation.
  - No forward-looking recommendations, treatment suggestions, or care instructions are permitted under `"Interpretation:"`.
  - Records with unspecified dates are preserved as "Date unspecified" without date hallucination or extrapolation.
- **Missing Data Handling:** If a record lacks an explicitly stated chief concern, `chief_concern` is set to `null` (`None`) and rendered as `"Not documented"`.
- **Mandatory Clinical Disclaimer:** Every generated summary automatically appends a standard clinical decision support disclaimer stating that the output is AI-generated and requires clinician verification.

### 4. Narrative Word Count Enforcement
- Prompts target a concise clinical narrative (~150 words).
- If the generated narrative exceeds 200 words, the summarizer re-prompts the LLM once with a specialized shortening prompt.
- As a last-resort safety fallback, sentence-boundary truncation is applied, and a warning flag is recorded on the summary schema.

### 5. Input-Completeness Confidence Scoring
- The `confidence_score` reflects **input-completeness** rather than arbitrary model certainty.
- Scores are computed from a transparent penalty matrix based on the availability of essential clinical components (encounters, clinical notes, lab observations, vital signs, and diagnostic reports).
- **Upper Bound Cap:** Confidence is capped at a maximum of **0.95**, formally encoding the principle that AI summarization inherently retains clinical uncertainty and requires human clinician review.

### 6. Dual-Layer Caching Architecture
- **Primary Cache (SQLite):** Persisted in `storage/clinical_platform.db` under the `clinical_summaries` table.
- **Secondary Cache (JSON Filesystem):** Backed up as individual JSON records under `storage/cache/summaries/summary_{patient_id}.json`.
- **Deterministic Cache Key:** Built from a SHA-256 hash of all input clinical documents, the prompt version (`PROMPT_VERSION = "v1.2"`), and the active model name:
  $$\text{Cache Key} = \text{SHA256}(\text{source documents}) + \text{":"} + \text{PROMPT\_VERSION} + \text{":"} + \text{model\_name}$$
- **Automatic Cache Invalidation:** Any mutation to a patient's source records alters the content hash and automatically triggers re-summarization. Similarly, bumping `PROMPT_VERSION` or changing the active model name safely invalidates prior cache entries.

### 7. Deterministic Testing & CI/CD Safety
- **Default Offline Pytest Suite:** All automated unit and integration tests execute entirely offline using `MockLLMClient`, guaranteeing zero external network dependencies, zero rate-limiting risk, and sub-second test execution.
- **Live Smoke Test Marker:** Live network tests against the Groq API are isolated under `@pytest.mark.live` and excluded by default in CI runs via `backend/pytest.ini`.

## Data Privacy

Only synthetic patient data is used for development and testing. No real patient information should be committed to this repository.

## License

To be determined.
