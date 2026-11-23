"""Integration tests for FastAPI frontend static routes and SPA delivery."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_serve_frontend_index(client):
    """Validates that GET / serves the clinical interface HTML5 shell."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    content = response.text
    assert "<title>EHR Media Intelligence" in content
    assert 'id="clinical-search-input"' in content
    assert 'id="patient-drawer"' in content
    assert 'id="results-cards-container"' in content


def test_serve_static_assets(client):
    """Validates that compiled Tailwind CSS, app CSS, JS modules, and SVGs are accessible."""
    # 1. Compiled Tailwind CSS
    res_css = client.get("/static/css/tailwind.min.css")
    assert res_css.status_code == 200
    assert "text/css" in res_css.headers.get("content-type", "")
    assert len(res_css.text) > 1000

    # 2. App custom CSS
    res_app_css = client.get("/static/css/app.css")
    assert res_app_css.status_code == 200
    assert "text/css" in res_app_css.headers.get("content-type", "")

    # 3. Main JS entrypoint
    res_js = client.get("/static/js/app.js")
    assert res_js.status_code == 200
    assert "javascript" in res_js.headers.get("content-type", "")
    assert "triggerSearch" in res_js.text

    # 4. Favicon SVG
    res_svg = client.get("/static/assets/favicon.svg")
    assert res_svg.status_code == 200
    assert "image/svg+xml" in res_svg.headers.get("content-type", "")
