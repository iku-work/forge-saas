from fastapi.testclient import TestClient

from app.main import app, check_database_ready, check_redis_ready


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0"}


def test_health_live_always_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_returns_200_when_dependencies_reachable() -> None:
    app.dependency_overrides[check_database_ready] = lambda: True
    app.dependency_overrides[check_redis_ready] = lambda: True
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_returns_503_when_a_dependency_is_unreachable() -> None:
    app.dependency_overrides[check_database_ready] = lambda: False
    app.dependency_overrides[check_redis_ready] = lambda: True
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
