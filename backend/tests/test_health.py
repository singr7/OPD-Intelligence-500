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


def test_environment_identity_is_stable_and_contains_no_infrastructure_secret():
    client = TestClient(create_app())
    resp = client.get("/environment")
    assert resp.status_code == 200
    body = resp.json()
    assert body["environment_id"] == "local"
    assert body["human_name"] == "Local development"
    assert body["api_contract_version"] == "2026-07-28"
    assert body["release_sha"] == "development"
    assert body["current_time"].endswith("Z")
    assert not (set(body) & {"database_url", "redis_url", "secret", "provider_key"})
