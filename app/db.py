import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    balance     INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS items (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    price  INTEGER NOT NULL CHECK (price > 0)
);

CREATE TABLE IF NOT EXISTS inventory (
    player_id  INTEGER NOT NULL REFERENCES players(id),
    item_id    INTEGER NOT NULL REFERENCES items(id),
    quantity   INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (player_id, item_id)
);

-- Every balance change gets a row here, so a player's balance can always be
-- rebuilt from their history. idempotency_key is unique per player, which is
-- what stops a retried request from charging or crediting twice.
CREATE TABLE IF NOT EXISTS ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id        INTEGER NOT NULL REFERENCES players(id),
    kind             TEXT NOT NULL CHECK (kind IN ('credit', 'purchase')),
    delta            INTEGER NOT NULL,
    balance_after    INTEGER NOT NULL,
    item_id          INTEGER REFERENCES items(id),
    quantity         INTEGER,
    idempotency_key  TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (player_id, idempotency_key)
);
"""

SEED_ITEMS = [
    (1, "Health Potion", 25),
    (2, "Iron Sword", 150),
    (3, "Leather Armor", 120),
    (4, "Fast Travel Scroll", 60),
]


def connect(path: str) -> sqlite3.Connection:
    # One connection per request. check_same_thread=False because FastAPI may
    # run sync endpoints on a thread pool.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT OR IGNORE INTO items (id, name, price) VALUES (?, ?, ?)", SEED_ITEMS)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    # BEGIN IMMEDIATE takes the write lock up front. Without it, two requests
    # can both read the same balance and then both try to write.
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
