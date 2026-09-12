import uuid

from tests.conftest import db_rows


def account(client, owner_id, account_type):
    return client.post(
        "/accounts",
        json={"owner_id": owner_id, "account_type": account_type},
    ).json()


def test_business_settlement_holds_then_posts_funds(client):
    suffix = uuid.uuid4().hex[:8]
    cash = account(client, f"cash-{suffix}", "cash")
    business = account(client, f"merchant-{suffix}", "business")
    external_bank = account(client, f"external-{suffix}", "external_bank")

    client.post(
        "/transactions/deposit",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "account_id": business["account_id"],
            "cash_account_id": cash["account_id"],
            "amount_minor": 75000,
        },
    )

    prepared = client.post(
        "/settlements/prepare",
        json={
            "batch_id": f"test-{suffix}",
            "external_bank_account_id": external_bank["account_id"],
        },
    )
    assert prepared.status_code == 201
    settlement = next(
        item for item in prepared.json()["settlements"]
        if item["account_id"] == business["account_id"]
    )

    held_balance = client.get(
        f"/accounts/{business['account_id']}/balance"
    ).json()
    assert held_balance["balance_minor"] == 75000
    assert held_balance["available_balance_minor"] == 0

    confirmed = client.post(
        f"/settlements/{settlement['settlement_id']}/confirm",
        json={"external_reference": f"bank-{suffix}"},
    )
    assert confirmed.status_code == 200
    transaction = confirmed.json()["transaction"]
    assert transaction["type"] == "settlement"

    business_balance = client.get(
        f"/accounts/{business['account_id']}/balance"
    ).json()
    bank_balance = client.get(
        f"/accounts/{external_bank['account_id']}/balance"
    ).json()
    assert business_balance["balance_minor"] == 0
    assert business_balance["available_balance_minor"] == 0
    assert bank_balance["balance_minor"] == 75000

    outbox = db_rows(
        "SELECT event_type FROM outbox_events WHERE transaction_id = %s",
        (transaction["transaction_id"],),
    )
    assert outbox == [{"event_type": "transaction.settlement"}]


def test_prepare_is_idempotent_per_batch_and_business_account(client):
    suffix = uuid.uuid4().hex[:8]
    cash = account(client, f"cash-{suffix}", "cash")
    business = account(client, f"merchant-{suffix}", "business")
    external_bank = account(client, f"external-{suffix}", "external_bank")
    client.post(
        "/transactions/deposit",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "account_id": business["account_id"],
            "cash_account_id": cash["account_id"],
            "amount_minor": 1200,
        },
    )
    body = {
        "batch_id": f"test-{suffix}",
        "external_bank_account_id": external_bank["account_id"],
    }
    first = client.post("/settlements/prepare", json=body).json()
    second = client.post("/settlements/prepare", json=body).json()
    assert first["settlements"][0]["settlement_id"] == second["settlements"][0]["settlement_id"]


def test_active_settlement_hold_blocks_transfer(client):
    suffix = uuid.uuid4().hex[:8]
    cash = account(client, f"cash-{suffix}", "cash")
    business = account(client, f"merchant-{suffix}", "business")
    recipient = account(client, f"recipient-{suffix}", "personal")
    external_bank = account(client, f"external-{suffix}", "external_bank")
    client.post(
        "/transactions/deposit",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "account_id": business["account_id"],
            "cash_account_id": cash["account_id"],
            "amount_minor": 1200,
        },
    )
    client.post(
        "/settlements/prepare",
        json={
            "batch_id": f"hold-{suffix}",
            "external_bank_account_id": external_bank["account_id"],
        },
    )

    transfer = client.post(
        "/transactions/transfer",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        json={
            "from_account_id": business["account_id"],
            "to_account_id": recipient["account_id"],
            "amount_minor": 1,
        },
    )

    assert transfer.status_code == 400
    assert transfer.json()["detail"] == "Insufficient funds"
