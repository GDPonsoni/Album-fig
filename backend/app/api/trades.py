import asyncio
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SESSION_COOKIE, current_user, user_from_token, verified_user
from app.db import SessionLocal, get_db
from app.models import Trade, User
from app.services import trades as service
from app.services.core import enforce_rate
from app.settings import settings

router = APIRouter(prefix="/trades", tags=["trades"])
log = logging.getLogger("album.trades")


class Hub:
    """Avisa os participantes conectados de que a troca mudou. O aviso não leva
    dados: o cliente relê o estado por REST, que é a única fonte de verdade.
    Funciona em um processo só; para vários workers, trocar por LISTEN/NOTIFY."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.listeners: dict[int, set[asyncio.Queue[str]]] = defaultdict(set)

    def publish(self, trade_id: int) -> None:
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._fan_out, trade_id)

    def _fan_out(self, trade_id: int) -> None:
        for queue in self.listeners.get(trade_id, ()):
            queue.put_nowait("changed")

    def subscribe(self, trade_id: int) -> asyncio.Queue[str]:
        self.loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()
        self.listeners[trade_id].add(queue)
        return queue

    def unsubscribe(self, trade_id: int, queue: asyncio.Queue[str]) -> None:
        self.listeners[trade_id].discard(queue)
        if not self.listeners[trade_id]:
            del self.listeners[trade_id]


hub = Hub()


class OfferItem(BaseModel):
    sticker_id: int
    quantity: int = Field(ge=1, le=99)


class OfferIn(BaseModel):
    items: list[OfferItem] = Field(max_length=50)


class AcceptIn(BaseModel):
    version: int


def _finish(db: Session, trade: Trade, user: User) -> dict:
    db.commit()
    hub.publish(trade.id)
    return service.trade_view(db, trade, user)


@router.post("", status_code=201)
def create(user: User = Depends(verified_user), db: Session = Depends(get_db)):
    enforce_rate(db, f"trade-create:{user.id}", "rate.trade_create_per_hour")
    return _finish(db, service.create_trade(db, user), user)


@router.get("/current")
def current(user: User = Depends(current_user), db: Session = Depends(get_db)):
    trade = service.current_trade(db, user)
    return {"token": trade.token if trade else None}


@router.get("/{token}")
def detail(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    view = service.visible_trade(db, user, token)
    db.commit()
    return view


@router.post("/{token}/join")
def join(token: str, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    return _finish(db, service.join_trade(db, user, token), user)


@router.put("/{token}/offer")
def offer(token: str, body: OfferIn, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    items = {item.sticker_id: item.quantity for item in body.items}
    return _finish(db, service.set_offer(db, user, token, items), user)


@router.post("/{token}/accept")
def accept(token: str, body: AcceptIn, user: User = Depends(verified_user), db: Session = Depends(get_db)):
    return _finish(db, service.accept(db, user, token, body.version), user)


@router.post("/{token}/cancel")
def cancel(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _finish(db, service.cancel(db, user, token), user)


def _ws_trade_id(session_token: str | None, trade_token: str) -> int | None:
    with SessionLocal() as db:
        user = user_from_token(db, session_token)
        if user is None:
            return None
        return db.scalar(
            select(Trade.id).where(
                Trade.token == trade_token,
                (Trade.creator_id == user.id) | (Trade.partner_id == user.id),
            )
        )


@router.websocket("/{token}/ws")
async def trade_socket(websocket: WebSocket, token: str):
    # Cookies acompanham WebSockets de qualquer site: a Origin precisa ser conferida.
    origin = websocket.headers.get("origin")
    if origin != settings.base_url and settings.is_prod:
        await websocket.close(code=4403)
        return
    trade_id = await run_in_threadpool(_ws_trade_id, websocket.cookies.get(SESSION_COOKIE), token)
    if trade_id is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    queue = hub.subscribe(trade_id)

    async def pump() -> None:
        while True:
            await websocket.send_text(await queue.get())

    sender = asyncio.create_task(pump())
    try:
        while True:
            await websocket.receive_text()  # só para perceber a desconexão
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        hub.unsubscribe(trade_id, queue)
