"""Smoke tests for the api service health contract and app factory."""

from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app


def test_create_app_returns_configured_instance():
    app = create_app()
    assert app.title == "OPD Intelligence Platform API"
    assert app.version == __version__


def test_health_route_contract():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "service": "api", "version": __version__}
