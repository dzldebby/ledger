import uuid
import pytest


def idem_headers():
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.fixture(scope="module")
def account_ids(client):
    cash = client.post("/accounts", json={
        "owner_id": "system",
        "account_type": "cash"
    }).json()
    customer = client.post("/accounts", json={
        "owner_id": "user-deposit-test",
        "account_type": "customer"
    }).json()
    return {"cash_account_id": cash["account_id"], "account_id": customer["account_id"]}


def test_deposit_returns_201(client, account_ids):
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 10000
    }, headers=idem_headers())
    assert response.status_code == 201


def test_deposit_returns_expected_fields(client, account_ids):
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 5000
    }, headers=idem_headers())
    data = response.json()
    assert data["type"] == "deposit"
    assert data["state"] == "posted"
    assert "transaction_id" in data
    assert len(data["postings"]) == 2


def test_deposit_postings_balance(client, account_ids):
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 2000
    }, headers=idem_headers())
    postings = response.json()["postings"]
    debits = sum(p["amount_minor"] for p in postings if p["side"] == "debit")
    credits = sum(p["amount_minor"] for p in postings if p["side"] == "credit")
    assert debits == credits


def test_deposit_zero_amount_returns_422(client, account_ids):
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 0
    }, headers=idem_headers())
    assert response.status_code == 422


def test_deposit_negative_amount_returns_422(client, account_ids):
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": -100
    }, headers=idem_headers())
    assert response.status_code == 422


def test_deposit_increases_customer_balance(client, account_ids):
    before = client.get(f"/accounts/{account_ids['account_id']}/balance").json()
    client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 1500
    }, headers=idem_headers())
    after = client.get(f"/accounts/{account_ids['account_id']}/balance").json()
    assert after["balance_minor"] == before["balance_minor"] + 1500


def test_deposit_same_account_and_cash_account_returns_400(client, account_ids):
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["account_id"],
        "amount_minor": 1000
    }, headers=idem_headers())
    assert response.status_code == 400


def test_deposit_missing_idempotency_key_returns_422(client, account_ids):
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 1000
    })
    assert response.status_code == 422


def test_deposit_retry_same_key_returns_same_transaction(client, account_ids):
    headers = idem_headers()
    body = {
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 777
    }
    first = client.post("/transactions/deposit", json=body, headers=headers)
    second = client.post("/transactions/deposit", json=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["transaction_id"] == second.json()["transaction_id"]


def test_deposit_retry_same_key_does_not_duplicate_balance(client, account_ids):
    headers = idem_headers()
    body = {
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 400
    }
    before = client.get(f"/accounts/{account_ids['account_id']}/balance").json()["balance_minor"]
    client.post("/transactions/deposit", json=body, headers=headers)
    client.post("/transactions/deposit", json=body, headers=headers)
    after = client.get(f"/accounts/{account_ids['account_id']}/balance").json()["balance_minor"]
    assert after == before + 400


def test_deposit_same_key_different_body_returns_409(client, account_ids):
    headers = idem_headers()
    client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 100
    }, headers=headers)
    response = client.post("/transactions/deposit", json={
        "account_id": account_ids["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 200
    }, headers=headers)
    assert response.status_code == 409


@pytest.fixture(scope="module")
def transfer_accounts(client, account_ids):
    alice = client.post("/accounts", json={"owner_id": "alice", "account_type": "customer"}).json()
    bob = client.post("/accounts", json={"owner_id": "bob", "account_type": "customer"}).json()
    client.post("/transactions/deposit", json={
        "account_id": alice["account_id"],
        "cash_account_id": account_ids["cash_account_id"],
        "amount_minor": 10000
    }, headers=idem_headers())
    return {"alice_id": alice["account_id"], "bob_id": bob["account_id"]}


def test_transfer_returns_201(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 1000
    }, headers=idem_headers())
    assert response.status_code == 201


def test_transfer_returns_expected_fields(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 500
    }, headers=idem_headers())
    data = response.json()
    assert data["type"] == "transfer"
    assert data["state"] == "posted"
    assert len(data["postings"]) == 2


def test_transfer_postings_balance(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 300
    }, headers=idem_headers())
    postings = response.json()["postings"]
    debits = sum(p["amount_minor"] for p in postings if p["side"] == "debit")
    credits = sum(p["amount_minor"] for p in postings if p["side"] == "credit")
    assert debits == credits


def test_transfer_moves_balance_correctly(client, transfer_accounts):
    before_alice = client.get(f"/accounts/{transfer_accounts['alice_id']}/balance").json()["balance_minor"]
    before_bob = client.get(f"/accounts/{transfer_accounts['bob_id']}/balance").json()["balance_minor"]

    client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 200
    }, headers=idem_headers())

    after_alice = client.get(f"/accounts/{transfer_accounts['alice_id']}/balance").json()["balance_minor"]
    after_bob = client.get(f"/accounts/{transfer_accounts['bob_id']}/balance").json()["balance_minor"]

    assert after_alice == before_alice - 200
    assert after_bob == before_bob + 200


def test_transfer_zero_amount_returns_422(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 0
    }, headers=idem_headers())
    assert response.status_code == 422


def test_transfer_negative_amount_returns_422(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": -100
    }, headers=idem_headers())
    assert response.status_code == 422


def test_transfer_insufficient_funds_returns_400(client):
    payer = client.post("/accounts", json={"owner_id": "poor-payer", "account_type": "customer"}).json()
    payee = client.post("/accounts", json={"owner_id": "payee", "account_type": "customer"}).json()
    response = client.post("/transactions/transfer", json={
        "from_account_id": payer["account_id"],
        "to_account_id": payee["account_id"],
        "amount_minor": 100
    }, headers=idem_headers())
    assert response.status_code == 400


def test_transfer_same_account_returns_400(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["alice_id"],
        "amount_minor": 100
    }, headers=idem_headers())
    assert response.status_code == 400


def test_transfer_unknown_account_returns_404(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": "00000000-0000-0000-0000-000000000000",
        "amount_minor": 100
    }, headers=idem_headers())
    assert response.status_code == 404


def test_transfer_missing_idempotency_key_returns_422(client, transfer_accounts):
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 100
    })
    assert response.status_code == 422


def test_transfer_retry_same_key_returns_same_transaction(client, transfer_accounts):
    headers = idem_headers()
    body = {
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 150
    }
    first = client.post("/transactions/transfer", json=body, headers=headers)
    second = client.post("/transactions/transfer", json=body, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["transaction_id"] == second.json()["transaction_id"]


def test_transfer_retry_same_key_does_not_duplicate_balance(client, transfer_accounts):
    headers = idem_headers()
    body = {
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 120
    }
    before = client.get(f"/accounts/{transfer_accounts['bob_id']}/balance").json()["balance_minor"]
    client.post("/transactions/transfer", json=body, headers=headers)
    client.post("/transactions/transfer", json=body, headers=headers)
    after = client.get(f"/accounts/{transfer_accounts['bob_id']}/balance").json()["balance_minor"]
    assert after == before + 120


def test_transfer_same_key_different_body_returns_409(client, transfer_accounts):
    headers = idem_headers()
    client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 50
    }, headers=headers)
    response = client.post("/transactions/transfer", json={
        "from_account_id": transfer_accounts["alice_id"],
        "to_account_id": transfer_accounts["bob_id"],
        "amount_minor": 60
    }, headers=headers)
    assert response.status_code == 409


def _create_account(client, owner_id, account_type):
    return client.post("/accounts", json={"owner_id": owner_id, "account_type": account_type}).json()


@pytest.fixture
def reversal_accounts(client):
    suffix = uuid.uuid4().hex[:8]
    cash = _create_account(client, f"system-{suffix}", "cash")
    customer = _create_account(client, f"customer-{suffix}", "customer")
    return {"cash_account_id": cash["account_id"], "account_id": customer["account_id"]}


def test_reversal_of_deposit_returns_201(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    response = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers())
    assert response.status_code == 201


def test_reversal_returns_expected_fields(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    response = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers())
    data = response.json()
    assert data["type"] == "reversal"
    assert data["state"] == "posted"
    assert data["reversal_of_id"] == deposit["transaction_id"]
    assert len(data["postings"]) == 2


def test_reversal_postings_are_opposite_of_original(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    reversal = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers()).json()

    original_by_account = {p["account_id"]: p["side"] for p in deposit["postings"]}
    reversal_by_account = {p["account_id"]: p["side"] for p in reversal["postings"]}

    for account_id, original_side in original_by_account.items():
        opposite = "credit" if original_side == "debit" else "debit"
        assert reversal_by_account[account_id] == opposite


def test_reversal_of_deposit_reverts_balances(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers())

    customer_balance = client.get(f"/accounts/{reversal_accounts['account_id']}/balance").json()["balance_minor"]
    cash_balance = client.get(f"/accounts/{reversal_accounts['cash_account_id']}/balance").json()["balance_minor"]

    assert customer_balance == 0
    assert cash_balance == 0


def test_reversal_of_transfer_reverts_balances(client, reversal_accounts):
    alice = _create_account(client, f"alice-{uuid.uuid4().hex[:8]}", "customer")
    bob = _create_account(client, f"bob-{uuid.uuid4().hex[:8]}", "customer")

    client.post("/transactions/deposit", json={
        "account_id": alice["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers())

    transfer = client.post("/transactions/transfer", json={
        "from_account_id": alice["account_id"],
        "to_account_id": bob["account_id"],
        "amount_minor": 300
    }, headers=idem_headers()).json()

    client.post(f"/transactions/{transfer['transaction_id']}/reverse", headers=idem_headers())

    alice_balance = client.get(f"/accounts/{alice['account_id']}/balance").json()["balance_minor"]
    bob_balance = client.get(f"/accounts/{bob['account_id']}/balance").json()["balance_minor"]

    assert alice_balance == 1000
    assert bob_balance == 0


def test_reversal_unknown_transaction_returns_404(client):
    response = client.post(
        "/transactions/00000000-0000-0000-0000-000000000000/reverse",
        headers=idem_headers()
    )
    assert response.status_code == 404


def test_reversal_already_reversed_returns_409(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers())
    response = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers())
    assert response.status_code == 409


def test_reversal_of_a_reversal_returns_400(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    reversal = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers()).json()

    response = client.post(f"/transactions/{reversal['transaction_id']}/reverse", headers=idem_headers())
    assert response.status_code == 400


def test_reversal_insufficient_funds_returns_400(client, reversal_accounts):
    bob = _create_account(client, f"bob-{uuid.uuid4().hex[:8]}", "customer")

    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    client.post("/transactions/transfer", json={
        "from_account_id": reversal_accounts["account_id"],
        "to_account_id": bob["account_id"],
        "amount_minor": 1000
    }, headers=idem_headers())

    response = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=idem_headers())
    assert response.status_code == 400


def test_reversal_missing_idempotency_key_returns_422(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    response = client.post(f"/transactions/{deposit['transaction_id']}/reverse")
    assert response.status_code == 422


def test_reversal_retry_same_key_returns_same_transaction(client, reversal_accounts):
    deposit = client.post("/transactions/deposit", json={
        "account_id": reversal_accounts["account_id"],
        "cash_account_id": reversal_accounts["cash_account_id"],
        "amount_minor": 1000
    }, headers=idem_headers()).json()

    headers = idem_headers()
    first = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=headers)
    second = client.post(f"/transactions/{deposit['transaction_id']}/reverse", headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["transaction_id"] == second.json()["transaction_id"]
