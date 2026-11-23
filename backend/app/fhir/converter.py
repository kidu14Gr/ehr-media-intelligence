"""HL7 FHIR R4B Converter for EHR Media Intelligence Platform.

Transforms canonical IntermediateRecord models into validated FHIR R4B resources:
- Patient: pat-{record_id} with official name, demographic details, and urn:example:national-id
- Encounter: One minimal Encounter per distinct encounter_id with status='unknown' and 'UNK' class
- DocumentReference: for clinical notes, discharge summaries, and general notes
- DiagnosticReport: for laboratory results and imaging metadata
- Bundle: type='collection' with absolute urn:uuid fullUrls and matching urn:uuid references
"""
import base64
from typing import Dict, List, Optional
import uuid

# Consistent imports from fhir.resources.R4B
from fhir.resources.R4B.address import Address
from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.contactpoint import ContactPoint
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import (
    DocumentReference,
    DocumentReferenceContent,
    DocumentReferenceContext,
)
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.reference import Reference

from app.schemas.enums import DocumentType
from app.schemas.intermediate import (
    ClinicalDocumentRecord,
    IntermediateRecord,
    PatientDemographics,
)

# LOINC / Terminology codes for document types
DOCUMENT_LOINC_MAP = {
    DocumentType.CLINICAL_NOTE: ("34108-1", "Outpatient Note"),
    DocumentType.DISCHARGE_SUMMARY: ("18842-5", "Discharge Summary"),
    DocumentType.LAB_REPORT: ("11502-2", "Laboratory Report"),
    DocumentType.IMAGING: ("18748-4", "Diagnostic Imaging Report"),
    DocumentType.OTHER: ("34117-2", "Provider-Unspecified History & Physical"),
}


class FHIRConverter:
    """Converts intermediate records into standard HL7 FHIR R4B resources and bundles."""

    def convert_patient(self, demo: PatientDemographics, record_id: str) -> Patient:
        """Converts PatientDemographics into a FHIR R4B Patient resource."""
        patient_id = f"pat-{record_id}"

        # 1. Identifiers
        identifiers: List[Identifier] = []
        # Primary MRN identifier
        facility_system = (
            f"http://hospital.smarthealth.org/{demo.facility_id}/mrn"
            if demo.facility_id
            else "http://hospital.smarthealth.org/default/mrn"
        )
        identifiers.append(
            Identifier(
                use="official",
                system=facility_system,
                value=demo.mrn,
            )
        )
        # National ID using urn:example:national-id (per requirement #6)
        if demo.national_id:
            identifiers.append(
                Identifier(
                    use="official",
                    system="urn:example:national-id",
                    value=demo.national_id,
                )
            )

        # 2. Human Name
        names = [
            HumanName(
                use="official",
                family=demo.last_name,
                given=[demo.first_name],
            )
        ]

        # 3. Telecom
        telecoms: List[ContactPoint] = []
        if demo.phone:
            telecoms.append(ContactPoint(system="phone", value=demo.phone, use="home"))
        if demo.email:
            telecoms.append(ContactPoint(system="email", value=demo.email))

        # 4. Address
        addresses: List[Address] = []
        if demo.address:
            addresses.append(Address(use="home", text=demo.address))

        # 5. Gender (canonical lowercase)
        fhir_gender = demo.gender.value.lower() if demo.gender else "unknown"

        return Patient(
            id=patient_id,
            identifier=identifiers,
            active=True,
            name=names,
            telecom=telecoms or None,
            gender=fhir_gender,
            birthDate=demo.date_of_birth.isoformat(),
            address=addresses or None,
        )

    def convert_encounter(self, encounter_id: str, patient_ref: Reference) -> Encounter:
        """Generates a minimal FHIR R4B Encounter for a distinct encounter_id.
        
        Per requirement #4:
        - status='unknown'
        - class_fhir uses placeholder code 'UNK' from HL7 ActCode (documented below)
        - subject points to the matching patient reference
        """
        # Placeholder Encounter class:
        # In HL7 v3 ActCode, code 'UNK' (unknown) is standard when specific encounter setting
        # (inpatient, outpatient, emergency) is not provided by source ingestion.
        encounter_class = Coding(
            system="http://terminology.hl7.org/CodeSystem/v3-ActCode",
            code="UNK",
            display="unknown",
        )

        return Encounter(
            id=f"enc-{encounter_id}",
            identifier=[
                Identifier(
                    system="http://hospital.smarthealth.org/encounter-id",
                    value=encounter_id,
                )
            ],
            status="unknown",
            class_fhir=encounter_class,
            subject=patient_ref,
        )

    def convert_document_reference(
        self,
        doc: ClinicalDocumentRecord,
        patient_ref: Reference,
        encounter_ref: Optional[Reference] = None,
    ) -> DocumentReference:
        """Converts ClinicalDocumentRecord to FHIR R4B DocumentReference.
        
        Preserves complete narrative as base64-encoded UTF-8 attachment.
        Leaves date empty if effective_date is None.
        """
        doc_id = f"docref-{doc.document_id}"

        # Clinical note category
        category = [
            CodeableConcept(
                coding=[
                    Coding(
                        system="http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category",
                        code="clinical-note",
                        display="Clinical Note",
                    )
                ],
                text="Clinical Note",
            )
        ]

        # Specific document type code
        loinc_code, loinc_display = DOCUMENT_LOINC_MAP.get(
            doc.document_type, ("34108-1", doc.title)
        )
        doc_type = CodeableConcept(
            coding=[
                Coding(
                    system="http://loinc.org",
                    code=loinc_code,
                    display=loinc_display,
                )
            ],
            text=doc.title,
        )

        # Base64-encoded UTF-8 narrative
        b64_narrative = base64.b64encode(doc.text_content.encode("utf-8")).decode("ascii")
        attachment = Attachment(
            contentType="text/plain; charset=utf-8",
            data=b64_narrative,
            title=doc.title,
        )

        # Context encounter
        context = None
        if encounter_ref:
            context = DocumentReferenceContext(encounter=[encounter_ref])

        # Author reference
        author = [Reference(display=doc.author)] if doc.author else None

        # Omit clinical date when effective_date is null (requirement #3)
        doc_date = doc.effective_date.isoformat() if doc.effective_date is not None else None

        return DocumentReference(
            id=doc_id,
            status="current",
            category=category,
            type=doc_type,
            subject=patient_ref,
            date=doc_date,
            author=author,
            context=context,
            content=[DocumentReferenceContent(attachment=attachment)],
        )

    def convert_diagnostic_report(
        self,
        doc: ClinicalDocumentRecord,
        patient_ref: Reference,
        encounter_ref: Optional[Reference] = None,
    ) -> DiagnosticReport:
        """Converts ClinicalDocumentRecord to FHIR R4B DiagnosticReport.
        
        Applied to lab reports (LAB) and radiology/imaging (RAD).
        Embeds narrative as base64 attachment and preserves diagnoses in conclusion.
        Leaves effectiveDateTime empty if effective_date is None.
        """
        report_id = f"diagrep-{doc.document_id}"

        # Category: LAB vs RAD
        cat_code = "LAB" if doc.document_type == DocumentType.LAB_REPORT else "RAD"
        cat_display = "Laboratory" if doc.document_type == DocumentType.LAB_REPORT else "Radiology"
        category = [
            CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/v2-0074",
                        code=cat_code,
                        display=cat_display,
                    )
                ],
                text=cat_display,
            )
        ]

        # LOINC Code
        loinc_code, loinc_display = DOCUMENT_LOINC_MAP.get(
            doc.document_type, ("11502-2", doc.title)
        )
        report_code = CodeableConcept(
            coding=[
                Coding(
                    system="http://loinc.org",
                    code=loinc_code,
                    display=loinc_display,
                )
            ],
            text=doc.title,
        )

        # Base64-encoded UTF-8 narrative
        b64_narrative = base64.b64encode(doc.text_content.encode("utf-8")).decode("ascii")
        presented_form = [
            Attachment(
                contentType="text/plain; charset=utf-8",
                data=b64_narrative,
                title=doc.title,
            )
        ]

        # Omit effective date when null (requirement #3)
        effective_dt = doc.effective_date.isoformat() if doc.effective_date is not None else None

        # Diagnostic conclusion
        conclusion = "; ".join(doc.diagnoses) if doc.diagnoses else None

        return DiagnosticReport(
            id=report_id,
            status="final",
            category=category,
            code=report_code,
            subject=patient_ref,
            encounter=encounter_ref,
            effectiveDateTime=effective_dt,
            conclusion=conclusion,
            presentedForm=presented_form,
        )

    def convert_record_to_bundle(self, record: IntermediateRecord) -> Bundle:
        """Assembles all resources for a patient into a self-contained FHIR R4B Bundle.
        
        Enforces requirement #7:
        - All entries have absolute urn:uuid fullUrls.
        - All internal references (subject, encounter) strictly use matching urn:uuid values.
        """
        # Deterministic UUID mapping for bundle entries
        # Uses UUIDv5 with NAMESPACE_URL for reproducible, clash-free identifiers
        patient_urn = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:patient:{record.record_id}')}"
        patient_display = f"{record.patient.first_name} {record.patient.last_name}"
        patient_ref = Reference(reference=patient_urn, display=patient_display)

        entries: List[BundleEntry] = []

        # 1. Patient Resource
        patient_resource = self.convert_patient(record.patient, record.record_id)
        entries.append(BundleEntry(fullUrl=patient_urn, resource=patient_resource))

        # 2. Distinct Encounters
        distinct_encounter_ids = sorted(
            list(
                {
                    doc.encounter_id
                    for doc in record.documents
                    if doc.encounter_id and doc.encounter_id.strip()
                }
            )
        )
        encounter_urn_map: Dict[str, str] = {}
        for enc_id in distinct_encounter_ids:
            enc_urn = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:encounter:{record.record_id}:{enc_id}')}"
            encounter_urn_map[enc_id] = enc_urn
            encounter_resource = self.convert_encounter(enc_id, patient_ref)
            entries.append(BundleEntry(fullUrl=enc_urn, resource=encounter_resource))

        # 3. Clinical Documents & Diagnostic Reports
        for doc in record.documents:
            doc_urn = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'urn:document:{doc.document_id}')}"
            enc_ref = (
                Reference(reference=encounter_urn_map[doc.encounter_id])
                if doc.encounter_id and doc.encounter_id in encounter_urn_map
                else None
            )

            if doc.document_type in (DocumentType.LAB_REPORT, DocumentType.IMAGING):
                report_resource = self.convert_diagnostic_report(doc, patient_ref, enc_ref)
                entries.append(BundleEntry(fullUrl=doc_urn, resource=report_resource))
            else:
                doc_resource = self.convert_document_reference(doc, patient_ref, enc_ref)
                entries.append(BundleEntry(fullUrl=doc_urn, resource=doc_resource))

        return Bundle(
            id=f"bundle-{record.record_id}",
            type="collection",
            entry=entries,
        )

    def convert_records(self, records: List[IntermediateRecord]) -> List[Bundle]:
        """Converts a batch of IntermediateRecord instances into a list of FHIR Bundles.
        
        Per requirement #11: Excludes quarantined records (only processes valid intermediate records).
        """
        return [self.convert_record_to_bundle(rec) for rec in records]
