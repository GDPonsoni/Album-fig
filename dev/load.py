"""Teste de carga leve: N jogadores simultâneos no servidor em execução.
Uso, de dentro de backend/: .venv/bin/python ../dev/load.py [jogadores] [rodadas] [pausa_s]
Pausa 0 é o pior caso; com 2 ou mais segundos o ritmo se parece com o de gente jogando.
Cria contas temporárias no banco configurado e as apaga no final."""
import random
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import httpx
from sqlalchemy import delete, select

from app.api.auth import hasher
from app.db import SessionLocal
from app.models import Album, AuditLog, PackGrant, PackType, User
from app.services.core import now

PLAYERS = int(sys.argv[1]) if len(sys.argv) > 1 else 50
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
THINK = float(sys.argv[3]) if len(sys.argv) > 3 else 0
BASE = "http://localhost:8000"
PASSWORD = "carga-12345"
timings: dict[str, list[float]] = defaultdict(list)
errors: list[str] = []


def timed(client: httpx.Client, name: str, method: str, path: str, **kwargs) -> httpx.Response:
    if THINK:
        time.sleep(random.uniform(0, 2 * THINK))
    start = time.perf_counter()
    response = client.request(method, path, **kwargs)
    timings[name].append(time.perf_counter() - start)
    if response.status_code >= 400:
        errors.append(f"{name}: {response.status_code} {response.text[:100]}")
    return response


def player(index: int) -> None:
    with httpx.Client(base_url=BASE, headers={"X-Requested-With": "album"}, timeout=30) as client:
        timed(client, "login", "POST", "/api/auth/login", json={"email": f"carga{index}@example.com", "password": PASSWORD})
        for _ in range(ROUNDS):
            album = timed(client, "album", "GET", "/api/album").json()
            page = album["pages"][index % len(album["pages"])]
            timed(client, "pagina", "GET", f"/api/album/orgs/{page['org_id']}/pages/{page['page_no']}")
            stock = timed(client, "estoque", "GET", "/api/packs").json()["stock"]
            if stock:
                opened = timed(client, "abrir_pacote", "POST", f"/api/packs/{stock[0]['id']}/open").json()
                for item in opened["items"]:
                    if item["is_new"]:
                        timed(client, "colar", "POST", f"/api/album/paste/{item['id']}")
                        break


with SessionLocal() as db:
    # Os pacotes de carga são do álbum em andamento (o do seed-demo).
    album = db.scalar(select(Album).where(Album.state == "ready", Album.starts_at <= now(), Album.ends_at > now()))
    if album is None:
        sys.exit("Nenhum álbum em andamento. Rode o seed-demo antes.")
    pack = db.scalar(select(PackType).where(PackType.album_id == album.id, PackType.slug == "comum"))
    password_hash = hasher.hash(PASSWORD)
    users = [
        User(
            email=f"carga{i}@example.com", nickname=f"carga{i}", password_hash=password_hash,
            email_verified_at=now(), university_id=album.university_id,
        )
        for i in range(PLAYERS)
    ]
    db.add_all(users)
    db.flush()
    db.add_all(
        PackGrant(user_id=u.id, album_id=album.id, pack_type_id=pack.id, source="admin") for u in users for _ in range(ROUNDS)
    )
    db.commit()
    ids = [u.id for u in users]

try:
    start = time.perf_counter()
    with ThreadPoolExecutor(PLAYERS) as pool:
        list(pool.map(player, range(PLAYERS)))
    elapsed = time.perf_counter() - start
finally:
    with SessionLocal() as db:
        db.execute(delete(AuditLog).where(AuditLog.user_id.in_(ids)))
        db.execute(delete(User).where(User.id.in_(ids)))
        db.commit()

print(f"{PLAYERS} jogadores, {ROUNDS} rodadas, {elapsed:.1f}s, {sum(map(len, timings.values()))} requisições, {len(errors)} erros")
for name, values in sorted(timings.items()):
    values.sort()
    print(f"{name:14} n={len(values):4} mediana={statistics.median(values) * 1000:6.0f}ms p95={values[int(len(values) * 0.95) - 1] * 1000:6.0f}ms máx={values[-1] * 1000:6.0f}ms")
for error in errors[:5]:
    print(error)
