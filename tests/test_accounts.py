def test_create_account_returns_201(client):
    response = client.post("/accounts", json={
        "owner_id": "user-123",
        "account_type": "customer"
    })
    assert response.status_code == 201


def test_create_account_returns_expected_fields(client):
    response = client.post("/accounts", json={
        "owner_id": "user-456",
        "account_type": "customer"
    })
    data = response.json()
    assert data["owner_id"] == "user-456"
    assert data["account_type"] == "customer"
    assert data["status"] == "active"
    assert "account_id" in data


def test_create_account_missing_owner_id_returns_422(client):
    response = client.post("/accounts", json={
        "account_type": "customer"
    })
    assert response.status_code == 422


def test_create_account_missing_account_type_returns_422(client):
    response = client.post("/accounts", json={
        "owner_id": "user-789"
    })
    assert response.status_code == 422


def test_get_balance_returns_zero_for_new_account(client):
    account = client.post("/accounts", json={
        "owner_id": "user-balance-test",
        "account_type": "customer"
    }).json()

    response = client.get(f"/accounts/{account['account_id']}/balance")
    assert response.status_code == 200
    data = response.json()
    assert data["account_id"] == account["account_id"]
    assert data["balance_minor"] == 0


def test_get_balance_returns_404_for_unknown_account(client):
    response = client.get("/accounts/00000000-0000-0000-0000-000000000000/balance")
    assert response.status_code == 404


def test_list_accounts_returns_200(client):
    response = client.get("/accounts")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_list_accounts_includes_created_account(client):
    created = client.post("/accounts", json={
        "owner_id": "user-list-test",
        "account_type": "customer"
    }).json()

    response = client.get("/accounts")
    account_ids = [a["account_id"] for a in response.json()]
    assert created["account_id"] in account_ids


def test_account_history_is_immutable_and_newest_first(client):
    import uuid

    suffix = uuid.uuid4().hex[:8]
    cash = client.post(
        "/accounts",
        json={"owner_id": f"history-cash-{suffix}", "account_type": "cash"},
    ).json()
    owner = client.post(
        "/accounts",
        json={"owner_id": f"history-owner-{suffix}", "account_type": "personal"},
    ).json()
    for amount in (1000, 2000):
        client.post(
            "/transactions/deposit",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={
                "account_id": owner["account_id"],
                "cash_account_id": cash["account_id"],
                "amount_minor": amount,
            },
        )

    response = client.get(f"/accounts/{owner['account_id']}/transactions")
    assert response.status_code == 200
    history = response.json()
    assert history["count"] == 2
    assert [
        item["posting"]["amount_minor"] for item in history["transactions"]
    ] == [2000, 1000]
