import pytest
from fastapi.testclient import TestClient

ADMIN = {"X-Admin-Key": "test-admin"}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("WALLET_DB", path)
    monkeypatch.setenv("ADMIN_KEY", "test-admin")
    return path


@pytest.fixture
def client(db_path):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def player(client):
    return client.post("/players", json={"name": "ava"}).json()


def credit(client, player_id, amount, key):
    return client.post(
        f"/players/{player_id}/credits",
        json={"amount": amount},
        headers={**ADMIN, "Idempotency-Key": key},
    )


def buy(client, player_id, item_id, key, quantity=1):
    return client.post(
        f"/players/{player_id}/purchases",
        json={"item_id": item_id, "quantity": quantity},
        headers={"Idempotency-Key": key},
    )
