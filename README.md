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
frontend/    Clinician web interface (vanilla ES6, Tailwind CSS, served via FastAPI)
data/        Sample, uploaded, and processed documents
storage/     Local database and vector persistence
```

## Development Status

- [x] **Task 1: Data Ingestion and Cleaning** — Heterogeneous parsing (JSON, CSV, plain-text), conservative normalization (MRN, DOB, gender), in-memory identity deduplication, and forensic audit logging.
- [x] **Task 2: FHIR R4B Normalization & Persistence** — Standards-compliant transformation to HL7 FHIR R4B, structural validation, and dual persistence (JSON + SQLite).
- [x] **Task 3: AI-Powered Clinical Summarization** — Groq-hosted open LLM inference, clinical safety guardrails (diagnoses verification, no hallucinated recommendations), dual caching (SQLite + JSON), and input-completeness confidence scoring.
- [x] **Task 4: Semantic Search Engine & Patient Query API** — Local SentenceTransformers embeddings loaded once at startup, persistent Chroma vector store, idempotent indexing with content-hash change detection, grouped patient search, date filtering with undated record exclusion, index-backend mismatch protection, and patient detail drawer endpoint.
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

## Task 4 Architecture & Design Decisions

### 1. Dual Embedding Architecture & Startup Pre-Loading
- **Default Local Embeddings:** High-throughput vectorization powered locally by `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions) running on CPU.
- **Singleton Startup Lifecycle:** The SentenceTransformers model is loaded **once** at application initialization via FastAPI lifespan management. Subsequent search queries and indexing passes execute with zero model loading overhead (< 65ms search latency).
- **Remote Hugging Face Backend:** Implements `HuggingFaceEmbeddingClient` targeting the hosted Feature Extraction Inference API with backoff retry on HTTP 503 (model warming) and rate limits. Configurable via `EMBEDDING_BACKEND=huggingface` and `HUGGINGFACE_API_KEY` for remote deployment without changing code.
- **Mock Embedder for Tests:** `MockEmbeddingClient` generates deterministic, normalized 384-dimensional unit vectors derived from content hashing, enabling sub-millisecond offline testing with zero network requests.

### 2. Index-Backend Mismatch Guard
- **Manifest Tracking:** Upon building or updating the vector store, an index manifest is atomically persisted to `storage/chroma/index_manifest.json`, recording the embedding backend, model name, dimension, and document count.
- **Semantic Space Protection:** Before executing queries, `VectorStoreManager.verify_backend_compatibility()` asserts that the active runtime configuration matches the backend that created the index. If a mismatch is detected (e.g. querying a `local` index using `huggingface` embeddings), the engine rejects the request with `IndexBackendMismatchError` (HTTP 409 Conflict), preventing vector space corruption.

### 3. Idempotent Indexing with Content-Hash Change Detection
- **Multi-Source Indexing:** Vectorizes clinical narrative documents (decoded base64 attachments from `DocumentReference`, observation narratives and `presentedForm` text from `DiagnosticReport`) and per-patient AI summaries.
- **Zero Direct PHI in Embeddings:** Patient names, medical record numbers (MRNs), and healthcare facility identifiers are stripped before embedding; only objective clinical observations and narratives enter the vector space.
- **Content-Hash Caching:** Each vector stores a SHA-256 `content_hash` in Chroma metadata. The indexer skips re-embedding unchanged records and exclusively computes embeddings for new or modified documents.
- **Stale Vector Pruning:** Automatically detects and purges vectors from Chroma whose underlying source records have been deleted in SQLite.

### 4. Summary Vector Date Integrity
- Summary vectors inherit the patient's **latest clinical source-document date** from `fhir_resources` (or `has_date=False`, `record_date=null` if all patient documents are undated).
- The vector store strictly never uses the AI summary generation timestamp (`created_at`) as a clinical record date.

### 5. Patient-Level Grouped Search & Relevance Thresholding
- **Patient Grouping:** The `POST /search` endpoint groups chunk-level matches by `patient_id`, selecting the highest relevance score for each patient and returning the top-5 distinct patients.
- **Configurable Relevance Floor:** Drops irrelevant tail matches below `MIN_RELEVANCE_SCORE` (default `0.15`, configurable via environment or query payload).
- **Sentence-Aware Snippets:** Summary snippets are extracted on clean sentence boundaries targeting $\ge 150$ characters, avoiding mid-word truncations.
- **Response Schema:** Each match provides `patient_id`, `patient_name`, `mrn`, `record_date`, `resource_type`, `score`, and `summary_snippet`.

### 6. Date Filtering & Undated Record Rules
- Records store date representations as integer `YYYYMMDD` (`record_date_int`) alongside an explicit boolean `has_date`.
- **Default (Unfiltered):** Undated records (`has_date=False`) remain in the candidate search pool.
- **Date-Filtered (`start_date` or `end_date`):** Undated records are **strictly excluded**, ensuring that temporal clinical queries never return unanchored records.

### 7. Patient Detail Drawer API & CORS
- **Endpoint `GET /patients/{id}`:** Powers the clinical drawer view for Task 5, returning patient demographics, the latest AI summary with safety disclaimers, and all linked FHIR resources.
- **Referential Consistency:** Automatically resolves internal `urn:uuid:...` encounter pointers so that all linked clinical documents and diagnostic reports share consistent administrative `encounter_id` values with their parent `Encounter` resource.
- **CORS Support:** Integrated `CORSMiddleware` supporting frontend development origins (`http://localhost:3000`, `http://localhost:5173`).

## Task 5 Architecture & Design Decisions

### 1. Clinician-Facing Interface & Zero-Build Runtime
- **Architecture:** Built with modern vanilla ES6 modules served directly as static files by FastAPI at `/` (`frontend/index.html`) and `/static` (`frontend/static/`).
- **Reviewer Ergonomics:** Requires **zero Node.js or npm build steps** at runtime. Evaluators and clinical reviewers simply start the FastAPI application (`uvicorn app.main:create_app`) and open `http://localhost:8000/`. Zero CORS configuration is required because the frontend and API share the exact same origin.
- **Security & XSS Defense:** All API-provided strings (patient names, clinical notes, summaries, and FHIR payloads) are rendered strictly through an isolated DOM builder (`dom.js`) using `textContent` and `createElement`. Zero raw `innerHTML` is executed with API payloads. Telemetry logs in browser console enforce zero PHI leakage.

### 2. Standalone Tailwind CSS Compilation & Fallback
- **Standalone Compiled Bundle:** Utility styles are compiled ahead-of-time into `frontend/static/css/tailwind.min.css` (32 KB minified), ensuring 100% offline functionality in air-gapped sandboxes without relying on external CDNs.
- **Rebuild Command:**
  ```bash
  cd ehr-media-intelligence && npx -y tailwindcss@3.4.17 -c frontend/tailwind.config.js -i frontend/static/css/input.css -o frontend/static/css/tailwind.min.css --minify
  ```
- **CDN Fallback:** Documented runtime fallback is available via the Tailwind Play CDN script (`<script src="https://cdn.tailwindcss.com"></script>`), dynamically loaded only if the compiled static CSS is absent.

### 3. Real-Time Semantic Search & Grouped Cards
- **Debounced Fetch:** User keystrokes are debounced at 300ms, with immediate search upon pressing `Enter`.
- **Request Cancellation:** Uses native `AbortController` to cancel in-flight stale requests when the user continues typing.
- **Relevance Score & Visual Progress:** Displays the raw relevance score (e.g. `0.73`) alongside a proportional visual score bar. Avoids arbitrary "% match" labels.
- **Patient Demographic Header:** Cards present patient name, surrogate ID, MRN, and DOB/age (when available from the API).
- **Explicit Date Badging:** Records with null timestamps display a prominent amber "Undated" badge, while dated records format cleanly in UTC.
- **Term Highlighting:** Safely highlights matched query words within the sentence-aware summary snippet using DOM `<mark>` elements without innerHTML risks.

### 4. Slide-Over Patient Detail Drawer & Safety Isolation
- **Slide-Over Experience:** Accessible modal drawer slide-over animated with smooth CSS transitions, respecting `prefers-reduced-motion: reduce`.
- **AI Safety Banner:** Visually prominent callout identifying the summary as AI-generated for clinical decision support assistance only. Surfaces the underlying model, prompt template version, and input-completeness score.
- **Fact vs. AI Interpretation Separation:** Separates objective documented findings from the model's clinical synthesis (marked under an `"Interpretation:"` callout with distinct indigo styling).
- **Conditional Unverified Diagnoses Box:** Clinical safety guard isolating diagnoses proposed by the LLM that were not corroborated verbatim in EHR text. **Renders only when unverified diagnoses exist**; stays completely hidden when the list is empty.
- **Missing Data Integrity:** Chief concern falls back to an explicit `"Not documented"` indicator when absent from clinical records.
- **Linked FHIR Resources:** Tabbed compact inspector displaying linked resource type, canonical ID, encounter pointer, and clinical date, with a collapsible formatted JSON preview and 1-click clipboard copy.

### 5. Accessibility & Keyboard Navigation (WCAG 2.1 AA)
- **Global Shortcut:** Pressing `/` from anywhere on the page focuses the primary search input.
- **Focus Trapping:** Opening the drawer traps keyboard focus within the panel via cyclic Tab navigation.
- **Escape to Close & Focus Return:** Pressing `Escape` closes the drawer and restores keyboard focus to the triggering card button.
- **Screen Reader Announcements:** `aria-live="polite"` status regions announce result counts and query state updates.
- **Contrast & Dark Mode:** Meets WCAG AA contrast standards ($\ge 4.5:1$ text contrast), with a toggleable dark mode theme persisted in `localStorage`.

### 6. Automated Verification Suite
- **Static Route & Asset Delivery:** Validated via `TestClient` in `tests/integration/test_frontend_routes.py`.
- **Search & Drawer Data Contracts:** Validated via `tests/integration/test_search_api.py` and `tests/integration/test_v13_quality_and_search.py`.

## Data Privacy

Only synthetic patient data is used for development and testing. No real patient information should be committed to this repository.

## License

To be determined.
