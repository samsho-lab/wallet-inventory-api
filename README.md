# Wallet & Inventory API

![tests](https://github.com/samsho-lab/wallet-inventory-api/actions/workflows/ci.yml/badge.svg)

A small REST service for an in-game currency wallet and item inventory, built with FastAPI and SQLite.

The endpoints are simple. The point of the project is the two bugs that show up in every real economy system:

1. **Double charges on retry.** A client times out, retries the purchase, and the player pays twice.
2. **Double spends under concurrency.** Two requests read the same balance at the same moment, both think they can afford the item, and both go through.

Both are handled here and both have tests that try to break them.

## How it works

**Idempotency keys.** Every credit and purchase needs an `Idempotency-Key` header. The ledger table has a `UNIQUE (player_id, idempotency_key)` constraint. If a request comes in with a key that's already been applied, the API returns the original result with `"replayed": true` and changes nothing. If the same key is reused for a *different* request, the API returns `409`.

**Atomic balance check.** The purchase deducts coins with a single conditional update:

```sql
UPDATE players SET balance = balance - :cost
WHERE id = :player AND balance >= :cost
```

If no row changed, the player couldn't afford it. There's no gap between checking the balance and writing it, so there's nothing to race. The whole purchase runs inside `BEGIN IMMEDIATE`, and a `CHECK (balance >= 0)` constraint is the last line of defense.

**Ledger.** Every balance change writes a row with the delta and the balance after it, so any player's balance can be rebuilt from their history. One of the tests checks that the ledger always sums to the current balance.

**Trust boundary.** Players can spend coins but can't create them. The credit endpoint requires an `X-Admin-Key` header. In a real game that call would come from the game server or a payment webhook, never from the player's client.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/players` | `{"name": "ava"}` |
| `GET` | `/players/{id}` | balance |
| `GET` | `/items` | item catalog (seeded on startup) |
| `POST` | `/players/{id}/credits` | admin only, `{"amount": 100}` + `Idempotency-Key` |
| `POST` | `/players/{id}/purchases` | `{"item_id": 1, "quantity": 2}` + `Idempotency-Key` |
| `GET` | `/players/{id}/inventory` | |
| `GET` | `/players/{id}/ledger` | full transaction history |

Interactive docs are at `/docs` once the server is running.

## Running it

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Or with Docker:

```bash
docker build -t wallet-api .
docker run -p 8000:8000 -e ADMIN_KEY=change-me wallet-api
```

Try it:

```bash
curl -X POST localhost:8000/players -H 'content-type: application/json' -d '{"name":"sam"}'

curl -X POST localhost:8000/players/1/credits \
  -H 'X-Admin-Key: dev-admin-key' -H 'Idempotency-Key: credit-0001' \
  -H 'content-type: application/json' -d '{"amount":200}'

curl -X POST localhost:8000/players/1/purchases \
  -H 'Idempotency-Key: purchase-0001' \
  -H 'content-type: application/json' -d '{"item_id":2}'
```

Run the purchase command twice and the second response comes back with `"replayed": true`. You're still only charged once.

## Tests

```bash
pytest -v
```

`tests/test_concurrency.py` fires 40 purchases from 16 threads at a player who can afford exactly 4. It checks that exactly 4 succeed and the balance lands on 0. A second test sends the same idempotency key from 20 threads and checks that it's applied once.

## Project layout

```
app/
  db.py        schema, connection setup, transaction helper
  service.py   business rules (credit, purchase, idempotency)
  main.py      FastAPI routes, validation, error mapping
tests/
  test_api.py          behavior through the HTTP layer
  test_concurrency.py  race-condition tests against the service layer
```

## What I'd change for production

- Swap SQLite for Postgres. The same conditional-update approach works there, and it handles far more concurrent writers.
- Real authentication (player sessions or JWTs) instead of trusting the player ID in the URL.
- Expire old idempotency keys instead of keeping them forever.
- Structured logging and metrics around failed purchases.
