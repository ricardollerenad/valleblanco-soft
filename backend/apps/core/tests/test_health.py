"""RED: el endpoint GET /api/v1/health/ todavía no existe."""

import pytest
from rest_framework.test import APIClient

HEALTH_URL = "/api/v1/health/"


@pytest.fixture
def api_client():
    return APIClient()


def test_health_returns_ok_without_login(api_client):
    response = api_client.get(HEALTH_URL)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_responds_json(api_client):
    response = api_client.get(HEALTH_URL)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_health_rejects_write_methods(api_client, method):
    response = getattr(api_client, method)(HEALTH_URL)

    assert response.status_code == 405
