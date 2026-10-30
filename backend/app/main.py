import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from app.api import admin, auth, dev, game, trades
from app.db import SessionLocal
from app.services.core import purge_rate_events
from app.services.mail import dev_outbox
from app.services.trades import expire_due_trades
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("album")

SWEEP_SECONDS = 3
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _sweep() -> list[int]:
    with SessionLocal() as db:
        purge_rate_events(db)
        return expire_due_trades(db)


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            for trade_id in await run_in_threadpool(_sweep):
                trades.hub.publish(trade_id)
        except Exception:
            log.exception("Falha na varredura de trocas")


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_sweeper())
    yield
    task.cancel()


app = FastAPI(title="Álbum da Calourada", lifespan=lifespan, docs_url=None if settings.is_prod else "/api/docs",
              openapi_url=None if settings.is_prod else "/api/openapi.json", redoc_url=None)


@app.middleware("http")
async def security(request: Request, call_next):
    # CSRF: sites externos não conseguem enviar este header sem CORS, e aqui não há CORS.
    if (
        request.method not in SAFE_METHODS
        and request.url.path.startswith("/api/")
        and request.headers.get("x-requested-with") != "album"
    ):
        return JSONResponse({"detail": "Requisição recusada."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    if settings.is_prod:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError):
    log.warning("Conflito de integridade: %s", exc.orig)
    return JSONResponse({"detail": "Conflito com um registro existente."}, status_code=409)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Erro não tratado em %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Algo deu errado. Tente de novo."}, status_code=500)


api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(auth.me_router)
api.include_router(game.router)
api.include_router(trades.router)
api.include_router(admin.router)

if not settings.is_prod:

    api.include_router(dev.router)

    @api.get("/dev/mailbox", tags=["dev"])
    def mailbox():
        return list(dev_outbox)


@api.get("/health", tags=["ops"])
def health():
    return {"ok": True}


app.include_router(api)

settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

# Em produção o próprio backend entrega o front compilado (mesma origem).
if settings.frontend_dist.exists():
    dist = settings.frontend_dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Não encontrado."}, status_code=404)
        candidate = (dist / path).resolve()
        if path and candidate.is_relative_to(dist) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")
