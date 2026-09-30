import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import service
from .db import connect, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_path = os.environ.get("WALLET_DB", "wallet.db")
    app.state.admin_key = os.environ.get("ADMIN_KEY", "dev-admin-key")
    init_db(app.state.db_path)
    yield


app = FastAPI(title="Wallet & Inventory API", version="1.0.0", lifespan=lifespan)


def get_conn(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_admin(request: Request, x_admin_key: str = Header(...)) -> None:
    # Crediting currency is a privileged action. In a game this would be the
    # game server or a payment webhook, never the player's client.
    if not secrets.compare_digest(x_admin_key, request.app.state.admin_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid admin key")


# ---- error mapping -------------------------------------------------------

@app.exception_handler(service.NotFound)
def _not_found(_: Request, exc: service.NotFound):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(service.InsufficientFunds)
def _insufficient(_: Request, exc: service.InsufficientFunds):
    return JSONResponse(status_code=409, content={"detail": "insufficient funds", "reason": str(exc)})


@app.exception_handler(service.Conflict)
def _conflict(_: Request, exc: service.Conflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# ---- request bodies ------------------------------------------------------

class NewPlayer(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class CreditRequest(BaseModel):
    amount: int = Field(gt=0, le=1_000_000)


class PurchaseRequest(BaseModel):
    item_id: int
    quantity: int = Field(default=1, gt=0, le=99)


IdempotencyKey = Header(..., alias="Idempotency-Key", min_length=8, max_length=64)


# ---- routes --------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/players", status_code=201)
def create_player(body: NewPlayer, conn=Depends(get_conn)):
    return service.create_player(conn, body.name)


@app.get("/players/{player_id}")
def get_player(player_id: int, conn=Depends(get_conn)):
    return service.get_player(conn, player_id)


@app.get("/items")
def list_items(conn=Depends(get_conn)):
    return service.list_items(conn)


@app.post("/players/{player_id}/credits", dependencies=[Depends(require_admin)])
def credit(player_id: int, body: CreditRequest, key: str = IdempotencyKey, conn=Depends(get_conn)):
    result = service.credit(conn, player_id, body.amount, key)
    return {"ledger_id": result.ledger_id, "balance": result.balance_after, "replayed": result.replayed}


@app.post("/players/{player_id}/purchases")
def purchase(player_id: int, body: PurchaseRequest, key: str = IdempotencyKey, conn=Depends(get_conn)):
    result = service.purchase(conn, player_id, body.item_id, body.quantity, key)
    return {"ledger_id": result.ledger_id, "balance": result.balance_after, "replayed": result.replayed}


@app.get("/players/{player_id}/inventory")
def inventory(player_id: int, conn=Depends(get_conn)):
    return service.get_inventory(conn, player_id)


@app.get("/players/{player_id}/ledger")
def ledger(player_id: int, conn=Depends(get_conn)):
    return service.get_ledger(conn, player_id)
