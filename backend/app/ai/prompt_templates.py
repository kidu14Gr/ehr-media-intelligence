"""Prompt templates and clinical safety instructions for LLM Summarization.

Enforces:
- Target word count of ~150 words (and strict maximum under 200 words)
- Separation of documented facts from clinical synthesis
- Strict anti-hallucination guardrail: NEVER invent diagnoses or findings
- Direct JSON output conforming to LLMRawSummaryOutput
"""
from typing import Dict, List
from app.ai.extractor import PatientClinicalContext

PROMPT_VERSION = "v1.2"

CLINICAL_SUMMARY_SYSTEM_PROMPT = """You are an expert Clinical AI Assistant specializing in Electronic Health Record (EHR) intelligence.
Your task is to synthesize heterogeneous patient clinical data into a concise, accurate, and structured clinical summary for attending physicians.

### CLINICAL SAFETY & ANTI-HALLUCINATION RULES:
1. FACTUAL GROUNDING: Rely strictly and solely on the provided patient records. NEVER invent, assume, or extrapolate diagnoses, lab findings, vital signs, or medications not documented in the source text.
2. STRICT DOCUMENTED STATEMENTS: The narrative summary must restate ONLY explicitly documented statements from the source records. NEVER add generic negative statements such as "denies symptoms", "denies new symptoms", or "denies chest pain" unless specifically documented in the record text.
3. SEPARATE DOCUMENTED FACTS: In the 'diagnoses' array, ONLY include diagnoses explicitly stated or definitively concluded in the clinical notes or diagnostic reports. Do not invent or deduce diagnoses.
4. STRICT KEY FINDINGS: The 'key_findings' array must contain ONLY values, measurements, or observations explicitly present in the records (e.g. "Glucose 95 mg/dL", "Lungs clear"). Do not add speculation.
5. INTERPRETATION CONSTRAINTS: Any clinical synthesis must be placed strictly within the 'narrative_summary' field labeled "Interpretation:". This section must summarize solely what the objective findings indicate. DO NOT invent recommendations, future care plans, lifestyle advice, or next steps in "Interpretation:" unless explicitly ordered in the record.
6. NO DATE INFERENCE: NEVER assign, assume, or guess dates for records marked "Date unspecified". Keep them undated.
7. CONCISENESS & LENGTH: The 'narrative_summary' field MUST be targeted at approximately 150 words, and MUST NEVER exceed 200 words. Be terse, objective, and clinically precise.
8. MISSING DATA: If no imaging, medical media, or notable anomalies are present, return an empty array [] for those fields. If chief concern is not documented, return null.
9. FORMAT: You must return ONLY valid, parseable JSON conforming to the requested schema. No markdown fences, no conversational preface, no trailing text.

### OUTPUT JSON SCHEMA:
{
  "chief_concern": "Primary presentation or visit reason, or null if not documented",
  "diagnoses": ["Explicitly documented diagnosis 1", "..."],
  "key_findings": ["Factual value or statement directly present in records", "..."],
  "recent_medical_media": [
    {
      "modality": "e.g., Chest X-Ray, CT Scan, Ultrasound, Lab Report, Clinical Document",
      "description": "Study or document description",
      "date": "YYYY-MM-DD or null",
      "findings": "Key findings or null"
    }
  ],
  "notable_anomalies": [
    {
      "description": "Description of anomalous lab value or clinical alert",
      "severity": "low | moderate | high | critical | unknown",
      "source_reference": "Document/Study ID or null"
    }
  ],
  "narrative_summary": "A cohesive clinical narrative restating only documented statements, ending with an 'Interpretation:' section summarizing what the findings indicate without unrecorded recommendations. TARGET ~150 WORDS. MAXIMUM 200 WORDS."
}
"""

SHORTEN_SUMMARY_PROMPT = """You are a Clinical AI Assistant. The following clinical narrative summary is too long ({current_word_count} words).
Strict clinical platform guidelines require the narrative summary to be strictly under 200 words (TARGET: ~150 words).

Please condense and shorten the summary while retaining all critical medical facts, diagnoses, and any 'Interpretation:' section:

ORIGINAL SUMMARY:
\"\"\"{original_summary}\"\"\"

Return ONLY the shortened narrative text (strictly under 200 words, no commentary, no JSON).
"""

REPAIR_JSON_PROMPT = """The previous output was supposed to be strictly valid JSON conforming to the Clinical Summary schema, but failed to parse with the following error:
Error: {parse_error}

Original response:
\"\"\"{malformed_json}\"\"\"

Please repair and output ONLY the valid JSON with no markdown formatting or commentary.
"""


def format_patient_prompt(context: PatientClinicalContext) -> str:
    """Formats the clinical context into the user prompt for LLM summarization.
    
    Per clinical privacy standards:
    - Excludes patient name, MRN, and facility.
    - Sends ONLY patient ID, age, and gender.
    - Retains clinical notes and diagnostic findings.
    """
    from datetime import date

    # Compute age from date of birth
    age_str = "Age unspecified"
    if context.date_of_birth:
        try:
            dob_date = date.fromisoformat(context.date_of_birth)
            today = date.today()
            age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))
            age_str = f"{age} years old"
        except Exception:
            age_str = context.date_of_birth

    lines: List[str] = [
        "PATIENT DEMOGRAPHICS (DE-IDENTIFIED):",
        f"- Patient ID: {context.patient_id}",
        f"- Age: {age_str}",
        f"- Gender: {context.gender}",
        "\nCLINICAL RECORDS & TIMELINE:",
    ]

    if not context.documents:
        lines.append("No clinical documents or diagnostic reports recorded for this patient.")
    else:
        for idx, doc in enumerate(context.documents, start=1):
            date_str = doc.date or "Date unspecified"
            lines.append(
                f"\n--- [Document {idx}] ID: {doc.document_id} | Type: {doc.resource_type} | Category: {doc.category} | Date: {date_str} ---"
            )
            lines.append(f"Title: {doc.title}")
            if doc.diagnoses:
                lines.append(f"Documented Diagnoses: {', '.join(doc.diagnoses)}")
            lines.append("Narrative / Report Content:")
            lines.append(doc.text_content.strip() or "(No narrative text provided)")

    lines.append(
        "\n\nGenerate the structured JSON summary targeting ~150 words for narrative_summary (strictly <= 200 words)."
    )
    return "\n".join(lines)
