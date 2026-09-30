"""Business rules for credits and purchases.

Kept separate from the HTTP layer so the rules can be tested without a web
server and so the route handlers stay thin.
"""
import sqlite3
from dataclasses import dataclass

from .db import transaction


class NotFound(Exception):
    pass


class InsufficientFunds(Exception):
    pass


class Conflict(Exception):
    pass


class IdempotencyConflict(Conflict):
    """Same idempotency key reused for a different request."""


@dataclass
class LedgerResult:
    ledger_id: int
    balance_after: int
    replayed: bool  # True when this was a retry of a request we already applied


def create_player(conn: sqlite3.Connection, name: str) -> dict:
    try:
        cur = conn.execute("INSERT INTO players (name) VALUES (?)", (name,))
    except sqlite3.IntegrityError:
        raise Conflict(f"player name '{name}' is taken")
    return get_player(conn, cur.lastrowid)


def get_player(conn: sqlite3.Connection, player_id: int) -> dict:
    row = conn.execute("SELECT id, name, balance FROM players WHERE id = ?", (player_id,)).fetchone()
    if row is None:
        raise NotFound(f"player {player_id} not found")
    return dict(row)


def _existing_entry(conn, player_id: int, key: str):
    return conn.execute(
        "SELECT id, kind, delta, balance_after, item_id, quantity FROM ledger "
        "WHERE player_id = ? AND idempotency_key = ?",
        (player_id, key),
    ).fetchone()


def credit(conn: sqlite3.Connection, player_id: int, amount: int, key: str) -> LedgerResult:
    with transaction(conn):
        prior = _existing_entry(conn, player_id, key)
        if prior is not None:
            if prior["kind"] != "credit" or prior["delta"] != amount:
                raise IdempotencyConflict("idempotency key already used for a different request")
            return LedgerResult(prior["id"], prior["balance_after"], replayed=True)

        cur = conn.execute("UPDATE players SET balance = balance + ? WHERE id = ?", (amount, player_id))
        if cur.rowcount == 0:
            raise NotFound(f"player {player_id} not found")

        balance = conn.execute("SELECT balance FROM players WHERE id = ?", (player_id,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO ledger (player_id, kind, delta, balance_after, idempotency_key) "
            "VALUES (?, 'credit', ?, ?, ?)",
            (player_id, amount, balance, key),
        )
        return LedgerResult(cur.lastrowid, balance, replayed=False)


def purchase(conn: sqlite3.Connection, player_id: int, item_id: int, quantity: int, key: str) -> LedgerResult:
    with transaction(conn):
        prior = _existing_entry(conn, player_id, key)
        if prior is not None:
            if prior["kind"] != "purchase" or prior["item_id"] != item_id or prior["quantity"] != quantity:
                raise IdempotencyConflict("idempotency key already used for a different request")
            return LedgerResult(prior["id"], prior["balance_after"], replayed=True)

        get_player(conn, player_id)
        item = conn.execute("SELECT price FROM items WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            raise NotFound(f"item {item_id} not found")

        cost = item["price"] * quantity

        # The balance check and the deduction happen in one statement, so there
        # is no window where another request can spend the same coins.
        cur = conn.execute(
            "UPDATE players SET balance = balance - ? WHERE id = ? AND balance >= ?",
            (cost, player_id, cost),
        )
        if cur.rowcount == 0:
            raise InsufficientFunds(f"purchase costs {cost}")

        conn.execute(
            "INSERT INTO inventory (player_id, item_id, quantity) VALUES (?, ?, ?) "
            "ON CONFLICT (player_id, item_id) DO UPDATE SET quantity = quantity + excluded.quantity",
            (player_id, item_id, quantity),
        )
        balance = conn.execute("SELECT balance FROM players WHERE id = ?", (player_id,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO ledger (player_id, kind, delta, balance_after, item_id, quantity, idempotency_key) "
            "VALUES (?, 'purchase', ?, ?, ?, ?, ?)",
            (player_id, -cost, balance, item_id, quantity, key),
        )
        return LedgerResult(cur.lastrowid, balance, replayed=False)


def list_items(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, name, price FROM items ORDER BY id")]


def get_inventory(conn: sqlite3.Connection, player_id: int) -> list[dict]:
    get_player(conn, player_id)
    rows = conn.execute(
        "SELECT i.id AS item_id, i.name, inv.quantity FROM inventory inv "
        "JOIN items i ON i.id = inv.item_id WHERE inv.player_id = ? AND inv.quantity > 0 ORDER BY i.id",
        (player_id,),
    )
    return [dict(r) for r in rows]


def get_ledger(conn: sqlite3.Connection, player_id: int) -> list[dict]:
    get_player(conn, player_id)
    rows = conn.execute(
        "SELECT id, kind, delta, balance_after, item_id, quantity, created_at FROM ledger "
        "WHERE player_id = ? ORDER BY id",
        (player_id,),
    )
    return [dict(r) for r in rows]
