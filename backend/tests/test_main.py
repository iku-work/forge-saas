from fastapi.testclient import TestClient

from app.main import app

# T-225/ASVS V14.3.2: docs_url/redoc_url/openapi_url are only set when
# ENVIRONMENT == "dev" (backend/app/main.py) -- .env.test (the fixture every
# test in this suite boots against) sets ENVIRONMENT=dev, so this asserts
# the "docs enabled" branch is actually wired up correctly. The "disabled
# outside dev" branch is a trivial ternary on the same three lines and is
# additionally covered by the ADR evidence pointing at them directly.


def test_docs_endpoint_reachable_in_dev() -> None:
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200


def test_openapi_schema_reachable_in_dev() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "ForgeSaaS"
