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

Project initialization in progress.

## Data Privacy

Only synthetic patient data is used for development and testing. No real patient information should be committed to this repository.

## License

To be determined.
