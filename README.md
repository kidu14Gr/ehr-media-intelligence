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
- [ ] **Task 3: AI-Powered Clinical Summarization**
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

## Data Privacy

Only synthetic patient data is used for development and testing. No real patient information should be committed to this repository.

## License

To be determined.
