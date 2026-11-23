"""FastAPI patient detail endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.exceptions import PatientNotFoundError, SearchError
from app.schemas.search import PatientDetailResponse
from app.search.service import ClinicalSearchService

router = APIRouter(prefix="/patients", tags=["patients"])


def get_search_service() -> ClinicalSearchService:
    """Dependency provider for search service."""
    return ClinicalSearchService()


@router.get(
    "/{patient_id}",
    response_model=PatientDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get patient demographics, latest AI summary, and linked FHIR resources",
)
def get_patient_detail(
    patient_id: str,
    service: ClinicalSearchService = Depends(get_search_service),
) -> PatientDetailResponse:
    """Returns complete patient profile for clinical drawer view in Task 5."""
    try:
        return service.get_patient_detail(patient_id)
    except PatientNotFoundError as not_found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(not_found),
        ) from not_found
    except SearchError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Patient retrieval error: {err}",
        ) from err
