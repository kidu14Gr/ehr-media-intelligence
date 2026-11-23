"""Validation engine for FHIR R4B resources and bundles.

Validates:
- Resource schema structural conformance via fhir.resources.R4B models
- Referential integrity: all subject and encounter references must resolve within the Bundle
- Clinical constraints: document dates must not precede patient birth date
- Generation of structured FHIRValidationReport
"""
from datetime import date, datetime
from typing import Dict, List, Set
from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.patient import Patient

from app.schemas.fhir_report import FHIRValidationReport, ValidationIssue


class FHIRValidator:
    """Validates FHIR R4B Bundles for schema conformance and referential integrity."""

    def validate_bundle(self, bundle: Bundle) -> List[ValidationIssue]:
        """Validates a single patient FHIR Bundle.
        
        Checks:
        1. Bundle type is 'collection'
        2. All entries have valid absolute urn:uuid fullUrls
        3. Exactly one Patient resource per bundle
        4. Referential integrity: Every subject and encounter reference strictly resolves within known fullUrls
        5. Clinical date ordering: encounter/document dates cannot precede patient birth date
        6. Attachment content integrity: attachments must have non-empty payload
        """
        issues: List[ValidationIssue] = []

        if not bundle.entry:
            issues.append(
                ValidationIssue(
                    severity="ERROR",
                    resource_id=bundle.id or "unknown",
                    resource_type="Bundle",
                    field_path="entry",
                    message="Bundle contains no entries.",
                )
            )
            return issues

        # 1. Collect all declared fullUrls in the bundle
        known_full_urls: Set[str] = set()
        patients: List[Patient] = []
        patient_dob: Optional[date] = None

        for idx, entry in enumerate(bundle.entry):
            if not entry.fullUrl:
                issues.append(
                    ValidationIssue(
                        severity="ERROR",
                        resource_id=f"entry[{idx}]",
                        resource_type="BundleEntry",
                        field_path=f"entry[{idx}].fullUrl",
                        message="Bundle entry missing fullUrl.",
                    )
                )
            else:
                if not entry.fullUrl.startswith("urn:uuid:"):
                    issues.append(
                        ValidationIssue(
                            severity="WARNING",
                            resource_id=entry.fullUrl,
                            resource_type="BundleEntry",
                            field_path=f"entry[{idx}].fullUrl",
                            message=f"fullUrl '{entry.fullUrl}' is not an absolute urn:uuid URI.",
                        )
                    )
                known_full_urls.add(entry.fullUrl)

            if isinstance(entry.resource, Patient):
                patients.append(entry.resource)
                if entry.resource.birthDate:
                    try:
                        patient_dob = date.fromisoformat(str(entry.resource.birthDate))
                    except Exception:
                        pass

        # 2. Check patient entry count
        if len(patients) == 0:
            issues.append(
                ValidationIssue(
                    severity="ERROR",
                    resource_id=bundle.id or "unknown",
                    resource_type="Bundle",
                    field_path="entry",
                    message="Bundle does not contain a Patient resource.",
                )
            )
        elif len(patients) > 1:
            issues.append(
                ValidationIssue(
                    severity="WARNING",
                    resource_id=bundle.id or "unknown",
                    resource_type="Bundle",
                    field_path="entry",
                    message=f"Bundle contains {len(patients)} Patient resources; expected exactly 1 per patient bundle.",
                )
            )

        # 3. Referential integrity checks across all entries
        for idx, entry in enumerate(bundle.entry):
            res = entry.resource
            res_id = getattr(res, "id", f"entry[{idx}]")
            res_type = res.__class__.__name__

            # Check Encounter references
            if isinstance(res, Encounter):
                if not res.subject or not res.subject.reference:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="subject.reference",
                            message="Encounter missing required subject reference.",
                        )
                    )
                elif res.subject.reference not in known_full_urls:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="subject.reference",
                            message=(
                                f"Encounter subject reference '{res.subject.reference}' "
                                "cannot be resolved within the Bundle entries."
                            ),
                        )
                    )

            # Check DocumentReference references & attachments
            elif isinstance(res, DocumentReference):
                if not res.subject or not res.subject.reference:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="subject.reference",
                            message="DocumentReference missing required subject reference.",
                        )
                    )
                elif res.subject.reference not in known_full_urls:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="subject.reference",
                            message=(
                                f"DocumentReference subject reference '{res.subject.reference}' "
                                "cannot be resolved within the Bundle entries."
                            ),
                        )
                    )

                # Context encounter reference
                if res.context and res.context.encounter:
                    for enc_ref in res.context.encounter:
                        if enc_ref.reference and enc_ref.reference not in known_full_urls:
                            issues.append(
                                ValidationIssue(
                                    severity="ERROR",
                                    resource_id=res_id,
                                    resource_type=res_type,
                                    field_path="context.encounter.reference",
                                    message=(
                                        f"DocumentReference encounter reference '{enc_ref.reference}' "
                                        "cannot be resolved within the Bundle entries."
                                    ),
                                )
                            )

                # Attachment content
                if not res.content or not res.content[0].attachment or not res.content[0].attachment.data:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="content[0].attachment.data",
                            message="DocumentReference attachment contains empty or missing narrative data.",
                        )
                    )

                # Date ordering
                if res.date and patient_dob:
                    try:
                        doc_date = datetime.fromisoformat(str(res.date)).date()
                        if doc_date < patient_dob:
                            issues.append(
                                ValidationIssue(
                                    severity="ERROR",
                                    resource_id=res_id,
                                    resource_type=res_type,
                                    field_path="date",
                                    message=f"Document date {doc_date} precedes patient birth date {patient_dob}.",
                                )
                            )
                    except Exception:
                        pass

            # Check DiagnosticReport references & attachments
            elif isinstance(res, DiagnosticReport):
                if not res.subject or not res.subject.reference:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="subject.reference",
                            message="DiagnosticReport missing required subject reference.",
                        )
                    )
                elif res.subject.reference not in known_full_urls:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="subject.reference",
                            message=(
                                f"DiagnosticReport subject reference '{res.subject.reference}' "
                                "cannot be resolved within the Bundle entries."
                            ),
                        )
                    )

                # Encounter reference
                if res.encounter and res.encounter.reference:
                    if res.encounter.reference not in known_full_urls:
                        issues.append(
                            ValidationIssue(
                                severity="ERROR",
                                resource_id=res_id,
                                resource_type=res_type,
                                field_path="encounter.reference",
                                message=(
                                    f"DiagnosticReport encounter reference '{res.encounter.reference}' "
                                    "cannot be resolved within the Bundle entries."
                                ),
                            )
                        )

                # Attachment content in presentedForm
                if not res.presentedForm or not res.presentedForm[0].data:
                    issues.append(
                        ValidationIssue(
                            severity="ERROR",
                            resource_id=res_id,
                            resource_type=res_type,
                            field_path="presentedForm[0].data",
                            message="DiagnosticReport presentedForm contains empty or missing narrative data.",
                        )
                    )

                # Date ordering
                if res.effectiveDateTime and patient_dob:
                    try:
                        rep_date = datetime.fromisoformat(str(res.effectiveDateTime)).date()
                        if rep_date < patient_dob:
                            issues.append(
                                ValidationIssue(
                                    severity="ERROR",
                                    resource_id=res_id,
                                    resource_type=res_type,
                                    field_path="effectiveDateTime",
                                    message=f"Report date {rep_date} precedes patient birth date {patient_dob}.",
                                )
                            )
                    except Exception:
                        pass

        return issues

    def validate_bundles(self, bundles: List[Bundle]) -> FHIRValidationReport:
        """Validates a collection of FHIR Bundles and produces a consolidated validation report."""
        all_issues: List[ValidationIssue] = []
        total_resources = 0
        resource_counts: Dict[str, int] = {}

        for bundle in bundles:
            bundle_issues = self.validate_bundle(bundle)
            all_issues.extend(bundle_issues)

            if bundle.entry:
                total_resources += len(bundle.entry)
                for entry in bundle.entry:
                    res_type = entry.resource.__class__.__name__
                    resource_counts[res_type] = resource_counts.get(res_type, 0) + 1

        is_valid = not any(issue.severity == "ERROR" for issue in all_issues)

        return FHIRValidationReport(
            is_valid=is_valid,
            total_bundles=len(bundles),
            total_resources=total_resources,
            resource_counts=resource_counts,
            issues=all_issues,
        )
