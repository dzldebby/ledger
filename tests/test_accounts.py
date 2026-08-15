from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_account_returns_201():
    response = client.post("/accounts", json={
        "owner_id": "user-123",
        "account_type": "customer"
    })
    assert response.status_code == 201


def test_create_account_returns_expected_fields():
    response = client.post("/accounts", json={
        "owner_id": "user-456",
        "account_type": "customer"
    })
    data = response.json()
    assert data["owner_id"] == "user-456"
    assert data["account_type"] == "customer"
    assert data["status"] == "active"
    assert "account_id" in data


def test_create_account_missing_owner_id_returns_422():
    response = client.post("/accounts", json={
        "account_type": "customer"
    })
    assert response.status_code == 422


def test_create_account_missing_account_type_returns_422():
    response = client.post("/accounts", json={
        "owner_id": "user-789"
    })
    assert response.status_code == 422
