from app import auth
from tests.conftest import provision_api_client


def test_health_does_not_require_auth(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_missing_api_key_returns_422(client):
    response = client.get("/accounts", headers={"X-API-Key": None})
    assert response.status_code == 422


def test_invalid_api_key_returns_401(client):
    response = client.get("/accounts", headers={"X-API-Key": "not-a-real-key"})
    assert response.status_code == 401


def test_valid_api_key_succeeds(client):
    response = client.get("/accounts")
    assert response.status_code == 200


def test_rate_limit_exceeded_returns_429(client, monkeypatch):
    monkeypatch.setattr(auth, "RATE_LIMIT_PER_MINUTE", 3)
    api_key = provision_api_client("rate-limit-test-client")

    headers = {"X-API-Key": api_key}
    responses = [client.get("/accounts", headers=headers) for _ in range(4)]

    assert [r.status_code for r in responses[:3]] == [200, 200, 200]
    assert responses[3].status_code == 429
