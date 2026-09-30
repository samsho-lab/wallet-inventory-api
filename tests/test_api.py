from .conftest import credit, buy


def test_new_player_starts_at_zero(client, player):
    assert player["balance"] == 0
    assert client.get(f"/players/{player['id']}").json()["balance"] == 0


def test_duplicate_player_name_is_rejected(client, player):
    assert client.post("/players", json={"name": "ava"}).status_code == 409


def test_credit_requires_admin_key(client, player):
    r = client.post(
        f"/players/{player['id']}/credits",
        json={"amount": 100},
        headers={"X-Admin-Key": "wrong", "Idempotency-Key": "credit-0001"},
    )
    assert r.status_code == 403


def test_purchase_moves_coins_into_inventory(client, player):
    pid = player["id"]
    credit(client, pid, 100, "credit-0001")

    r = buy(client, pid, item_id=1, key="buy-000001", quantity=2)  # 2 x 25
    assert r.status_code == 200
    assert r.json()["balance"] == 50

    inv = client.get(f"/players/{pid}/inventory").json()
    assert inv == [{"item_id": 1, "name": "Health Potion", "quantity": 2}]


def test_insufficient_funds_changes_nothing(client, player):
    pid = player["id"]
    credit(client, pid, 100, "credit-0001")

    r = buy(client, pid, item_id=2, key="buy-000001")  # sword costs 150
    assert r.status_code == 409
    assert client.get(f"/players/{pid}").json()["balance"] == 100
    assert client.get(f"/players/{pid}/inventory").json() == []


def test_retried_purchase_is_only_charged_once(client, player):
    pid = player["id"]
    credit(client, pid, 100, "credit-0001")

    first = buy(client, pid, item_id=1, key="buy-retry-1")
    second = buy(client, pid, item_id=1, key="buy-retry-1")

    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert second.json()["balance"] == first.json()["balance"] == 75
    assert client.get(f"/players/{pid}/inventory").json()[0]["quantity"] == 1


def test_reusing_key_for_different_request_is_rejected(client, player):
    pid = player["id"]
    credit(client, pid, 500, "credit-0001")
    buy(client, pid, item_id=1, key="buy-000001")

    r = buy(client, pid, item_id=2, key="buy-000001")
    assert r.status_code == 409


def test_unknown_player_and_item(client, player):
    assert client.get("/players/9999").status_code == 404
    credit(client, player["id"], 100, "credit-0001")
    assert buy(client, player["id"], item_id=42, key="buy-000001").status_code == 404


def test_validation_rejects_bad_input(client, player):
    pid = player["id"]
    assert credit(client, pid, -5, "credit-0001").status_code == 422
    assert buy(client, pid, item_id=1, key="short").status_code == 422
    assert buy(client, pid, item_id=1, key="buy-000001", quantity=0).status_code == 422


def test_ledger_always_adds_up_to_balance(client, player):
    pid = player["id"]
    credit(client, pid, 300, "credit-0001")
    buy(client, pid, item_id=1, key="buy-000001")
    buy(client, pid, item_id=4, key="buy-000002", quantity=2)
    credit(client, pid, 40, "credit-0002")

    ledger = client.get(f"/players/{pid}/ledger").json()
    balance = client.get(f"/players/{pid}").json()["balance"]

    assert sum(e["delta"] for e in ledger) == balance == 300 - 25 - 120 + 40
    assert ledger[-1]["balance_after"] == balance
