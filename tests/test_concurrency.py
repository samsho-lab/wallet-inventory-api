"""Hammer the purchase path from many threads at once.

This is the classic double-spend bug: two requests both read balance=100,
both decide they can afford a 25-coin item, and both write. With enough of
them the player ends up with more items than they paid for, or a negative
balance. These tests go straight at the service layer with separate
connections, the same way concurrent HTTP requests would.
"""
from concurrent.futures import ThreadPoolExecutor

from app import service
from app.db import connect, init_db


def _setup(db_path, starting_balance):
    init_db(db_path)
    conn = connect(db_path)
    player = service.create_player(conn, "racer")
    service.credit(conn, player["id"], starting_balance, "seed-credit")
    conn.close()
    return player["id"]


def _attempt(db_path, player_id, key):
    conn = connect(db_path)
    try:
        service.purchase(conn, player_id, item_id=1, quantity=1, key=key)
        return "ok"
    except service.InsufficientFunds:
        return "broke"
    finally:
        conn.close()


def test_no_double_spend_under_concurrency(db_path):
    player_id = _setup(db_path, starting_balance=100)  # enough for exactly 4 potions

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda i: _attempt(db_path, player_id, f"concurrent-{i}"), range(40)))

    conn = connect(db_path)
    balance = service.get_player(conn, player_id)["balance"]
    inventory = service.get_inventory(conn, player_id)
    conn.close()

    assert results.count("ok") == 4
    assert results.count("broke") == 36
    assert balance == 0
    assert inventory[0]["quantity"] == 4


def test_same_key_from_many_threads_applies_once(db_path):
    player_id = _setup(db_path, starting_balance=1000)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda _: _attempt(db_path, player_id, "one-shared-key"), range(20)))

    conn = connect(db_path)
    assert service.get_player(conn, player_id)["balance"] == 975
    assert service.get_inventory(conn, player_id)[0]["quantity"] == 1
    conn.close()
