"""FastAPI search endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.exceptions import IndexBackendMismatchError, SearchError
from app.schemas.search import SearchRequest, SearchResponse
from app.search.service import ClinicalSearchService

router = APIRouter(tags=["search"])


def get_search_service() -> ClinicalSearchService:
    """Dependency provider for search service."""
    return ClinicalSearchService()


@router.post(
    "/search",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Semantic search over clinical documents and AI summaries",
)
def search_clinical_records(
    request: SearchRequest,
    service: ClinicalSearchService = Depends(get_search_service),
) -> SearchResponse:
    """Performs semantic similarity search grouped by patient.

    Filters by resource type and date range (YYYY-MM-DD). Undated records are
    included when no date filter is specified, and excluded when a date filter is applied.
    """
    try:
        return service.search(request)
    except IndexBackendMismatchError as mismatch:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(mismatch),
        ) from mismatch
    except SearchError as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search service error: {err}",
        ) from err
